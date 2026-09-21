"""Client-side glue between :mod:`toontown.town.Street` and the action systems.

A :class:`StreetRun` is created when the Toon enters a street and destroyed
when they leave.  It owns the HUD, the tier-select and loadout panels, tells
the street's suit planner when the run starts/ends and turns the local Toon
going sad into the classic "teleport to the playground" flow.

Tier choices are remembered per street for the session so popping in and out
of a shop does not re-prompt.
"""

import time

from direct.directnotify import DirectNotifyGlobal
from direct.showbase.DirectObject import DirectObject
from direct.task import Task

from toontown.action import ActionGlobals
from toontown.action.ui.ActionHUD import ActionHUD
from toontown.action.ui.LoadoutPanel import LoadoutPanel
from toontown.action.ui.TierSelectPanel import TierSelectPanel
from toontown.action.ui.RunEndPanel import RunEndPanel
from toontown.hood import ZoneUtil
from toontown.toonbase import TTLocalizer


class StreetRun(DirectObject):
    notify = DirectNotifyGlobal.directNotify.newCategory('StreetRun')

    # Session memory shared by every StreetRun instance.
    rememberedTiers = {}
    lastLeftTime = {}
    # Coming back from a shop within this window skips the tier prompt.
    REPROMPT_GRACE = 300.0
    PLANNER_WAIT_TIMEOUT = 12.0
    END_PANEL_WAIT_TIMEOUT = 10.0

    def __init__(self, street):
        DirectObject.__init__(self)
        self.street = street
        self.branchZone = street.loader.branchZone
        self.canonicalBranch = ZoneUtil.getCanonicalBranchZone(street.loader.zoneId)
        self.streetName = self._lookupStreetName()
        self.tier = self.rememberedTiers.get(self.branchZone, ActionGlobals.DEFAULT_TIER)
        self.hud = None
        self.tierPanel = None
        self.loadoutPanel = None
        self.tierDoneEvent = street.uniqueName('actionTierDone') if hasattr(street, 'uniqueName') \
            else 'actionTierDone-%d' % self.branchZone
        self.loadoutDoneEvent = 'actionLoadoutDone-%d' % self.branchZone
        self.pendingRunTier = None
        self.runStarted = False
        self.plannerWaitTask = None
        self.entered = False
        self.endMode = None
        # Set while the Toon is inside a real-time cog building; cleared when
        # they step back onto a street (or the boarding is rejected).
        self.buildingActive = False
        # True while this run is detached from its street (shop, tunnel or
        # building).  A resumed run must not treat a RUN_STARTED announce as
        # a fresh start and wipe the rolling kill counters.
        self.runPreserved = False
        # True once the Toon cleared a building's top floor this run; the end
        # report then reads as a successful extraction instead of an escape.
        self.extracted = False
        # Hood captured when the run detaches into a building interior; the
        # death hook needs it because the Street object is already torn down
        # by the time the Toon can go sad inside.
        self._buildingHoodId = None
        self.streetTransitionPending = False
        self.pendingEndPanel = None
        self.endPanelWaitTask = None
        base.actionStreetRun = self

    # ------------------------------------------------------------------
    def _lookupStreetName(self):
        names = TTLocalizer.GlobalStreetNames.get(self.canonicalBranch)
        if names:
            return names[-1]
        return TTLocalizer.ActionUnknownStreet

    def getStreetName(self):
        return self.streetName

    def getPlanner(self):
        planner = getattr(base.cr, 'currSuitPlanner', None)
        if planner is not None and getattr(planner, 'zoneId', None) not in (None, self.branchZone):
            # A planner from a previous street can linger for a frame.
            if ZoneUtil.getBranchZone(planner.zoneId) != self.branchZone:
                return None
        return planner

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def enter(self):
        if self.entered:
            return
        self.entered = True
        self.hud = ActionHUD(self.streetName)
        self.accept(base.localAvatar.uniqueName('died'), self.__handleLocalToonDied)
        self.accept('action-planner-ready', self.__handlePlannerReady)
        self.accept('action-run-extracted', self.markExtracted)
        planner = self.getPlanner()
        if planner is not None:
            self.__primeHudFromPlanner(planner)

    def exit(self, force=False, deferEndPanel=False):
        if not self.entered and not force:
            return
        self.entered = False
        self.ignoreAll()
        self._stopPlannerWait()
        if self.buildingActive and not force:
            self.closeTierSelect()
            self.closeLoadout()
            return
        if self.runStarted and self.endMode is None:
            if deferEndPanel:
                # Do not put a modal DirectGUI overlay in the middle of the
                # Street -> hood -> playground handoff.  The old behavior
                # created the report from Street.exit(), which could leave the
                # new playground loaded underneath a panel that still owned
                # the transition.  Keep the snapshot alive until the safe
                # zone announces that it is ready.
                self._queueEndPanel()
            else:
                self._showEndPanel(escaped=True)
        if self.runStarted:
            planner = self.getPlanner()
            if planner is not None:
                planner.d_leaveStreetRun()
            self.runStarted = False
        self.lastLeftTime[self.branchZone] = time.time()
        self.closeTierSelect()
        self.closeLoadout()
        if self.hud is not None:
            self.hud.destroy()
            self.hud = None

    def preserveForStreetTransition(self):
        """Detach from the old Street without ending the shared run."""
        if not self.entered:
            return
        self.entered = False
        self.streetTransitionPending = True
        self.runPreserved = True
        if self.hud is not None:
            self.hud.runPreserved = True
        self.ignoreAll()
        self._stopPlannerWait()
        self.closeTierSelect()
        self.closeLoadout()
        # Keep the HUD, tier, and runStarted flag alive.  The destination
        # street reattaches this same object after its planner generates.
        if self.buildingActive:
            # The interior place FSM owns deaths while the run is detached
            # into a building; without this hook going sad inside would leave
            # a sad Toon stranded in the room.
            self._buildingHoodId = self.street.loader.hood.id if self.street is not None else None
            self.accept(base.localAvatar.uniqueName('died'), self.__handleBuildingToonDied)

    def destroy(self, deferEndPanel=False):
        # A preserved building/connector run is intentionally detached from
        # its old Street.  If that Street is later being destroyed for a
        # real exit, force the teardown so an already-detached run cannot
        # remain registered forever.
        self.exit(force=True, deferEndPanel=deferEndPanel)
        if getattr(base, 'actionStreetRun', None) is self:
            base.actionStreetRun = None
        self.street = None

    def _queueEndPanel(self):
        if self.hud is None:
            return
        self.pendingEndPanel = (self.streetName, self.hud.getRunSummary())
        self.acceptOnce('enterPlayground', self.__showQueuedEndPanel)
        taskName = 'actionRunEndPanelWait-%d' % id(self)
        self.endPanelWaitTask = taskMgr.doMethodLater(
            self.END_PANEL_WAIT_TIMEOUT, self.__showQueuedEndPanelFallback, taskName)

    def __showQueuedEndPanelFallback(self, task):
        self.endPanelWaitTask = None
        self.__showQueuedEndPanel()
        return Task.done

    def __showQueuedEndPanel(self):
        if self.endPanelWaitTask is not None:
            taskMgr.remove(self.endPanelWaitTask)
            self.endPanelWaitTask = None
        self.ignore('enterPlayground')
        if self.pendingEndPanel is None:
            return
        streetName, summary = self.pendingEndPanel
        self.pendingEndPanel = None
        existing = getattr(base, 'actionRunEndPanel', None)
        if existing is not None:
            existing.destroy()
        self.endMode = 'escaped'
        base.actionRunEndPanel = RunEndPanel(streetName, summary, True,
                                             extracted=self.extracted)

    def beginBuilding(self):
        self.buildingActive = True
        self.extracted = False

    def cancelBuilding(self):
        self.buildingActive = False
        self.extracted = False

    def markExtracted(self):
        """The local Toon cleared a building's top floor this run."""
        self.extracted = True

    def returnToStreet(self, street):
        continueRun = self.streetTransitionPending or self.buildingActive
        self.street = street
        self.branchZone = street.loader.branchZone
        self.canonicalBranch = ZoneUtil.getCanonicalBranchZone(street.loader.zoneId)
        self.streetName = self._lookupStreetName()
        self.buildingActive = False
        self.streetTransitionPending = False
        self.entered = True
        self._buildingHoodId = None
        # Drop the interior death hook before re-registering the street one;
        # messenger accepts with different callbacks would otherwise stack.
        self.ignore(base.localAvatar.uniqueName('died'))
        self.accept(base.localAvatar.uniqueName('died'), self.__handleLocalToonDied)
        self.accept('action-planner-ready', self.__handlePlannerReady)
        self.accept('action-run-extracted', self.markExtracted)
        if continueRun:
            # A new street has a new distributed planner.  Re-register the
            # preserved run there without reopening tier selection.
            planner = self.getPlanner()
            if planner is not None:
                self.__sendRunRequest(planner)
            else:
                self.pendingRunTier = self.tier
                self._startPlannerWait()
        elif planner is not None:
            self.__primeHudFromPlanner(planner)

    # ------------------------------------------------------------------
    # Tier selection
    # ------------------------------------------------------------------
    def needsTierSelect(self, requestStatus):
        """Prompt unless we just stepped out of a shop on this same street."""
        how = (requestStatus or {}).get('how')
        remembered = self.branchZone in self.rememberedTiers
        recent = time.time() - self.lastLeftTime.get(self.branchZone, 0.0) < self.REPROMPT_GRACE
        if how in ('doorIn', 'elevatorIn') and remembered and recent:
            return False
        return True

    def openTierSelect(self):
        self.closeTierSelect()
        self.acceptOnce(self.tierDoneEvent, self.__handleTierChosen)
        self.tierPanel = TierSelectPanel(self.streetName, self.tier, self.tierDoneEvent,
                                         loadoutCallback=self.openLoadout)

    def closeTierSelect(self):
        self.ignore(self.tierDoneEvent)
        if self.tierPanel is not None:
            self.tierPanel.destroy()
            self.tierPanel = None

    def __handleTierChosen(self, tier):
        self.closeTierSelect()
        self.closeLoadout()
        self.startRun(tier)
        street = self.street
        if street is not None and street.fsm.getCurrentState().getName() == 'tierSelect':
            street.fsm.request('walk')

    def startRun(self, tier=None):
        if self.runStarted:
            # Already registered with a planner (preserved resume, or a
            # double prompt); a second request would reset the rolling run.
            return
        if tier is not None:
            self.tier = max(ActionGlobals.MIN_TIER, min(ActionGlobals.MAX_TIER, int(tier)))
        self.rememberedTiers[self.branchZone] = self.tier
        messenger.send('action-tier', [self.tier])
        planner = self.getPlanner()
        if planner is not None:
            self.__sendRunRequest(planner)
        else:
            self.pendingRunTier = self.tier
            self._startPlannerWait()

    def __sendRunRequest(self, planner):
        planner.d_requestStreetRun(self.tier)
        self.runStarted = True
        self.pendingRunTier = None
        self._stopPlannerWait()

    def __handlePlannerReady(self, planner):
        if ZoneUtil.getBranchZone(planner.zoneId) != self.branchZone:
            return
        self.__primeHudFromPlanner(planner)
        if self.pendingRunTier is not None:
            self.__sendRunRequest(planner)

    def __primeHudFromPlanner(self, planner):
        pressure, stage = planner.getActionPressure()
        messenger.send('action-pressure', [pressure, stage, stage])
        messenger.send('action-objectives', [planner.getActionObjectives()])

    def _startPlannerWait(self):
        self._stopPlannerWait()
        self.plannerWaitStarted = globalClock.getFrameTime()
        self.plannerWaitTask = taskMgr.add(self.__plannerWaitTask, 'actionPlannerWait-%d' % self.branchZone)

    def _stopPlannerWait(self):
        if self.plannerWaitTask is not None:
            taskMgr.remove(self.plannerWaitTask)
            self.plannerWaitTask = None

    def __plannerWaitTask(self, task):
        planner = self.getPlanner()
        if planner is not None and self.pendingRunTier is not None:
            self.__sendRunRequest(planner)
            return Task.done
        if globalClock.getFrameTime() - self.plannerWaitStarted > self.PLANNER_WAIT_TIMEOUT:
            self.notify.warning('No suit planner arrived for branch %s; run not started' % self.branchZone)
            self.plannerWaitTask = None
            return Task.done
        return Task.cont

    # ------------------------------------------------------------------
    # Loadout
    # ------------------------------------------------------------------
    def openLoadout(self):
        if self.loadoutPanel is not None:
            return
        self.acceptOnce(self.loadoutDoneEvent, self.closeLoadout)
        self.loadoutPanel = LoadoutPanel(self.loadoutDoneEvent)

    def closeLoadout(self):
        self.ignore(self.loadoutDoneEvent)
        if self.loadoutPanel is not None:
            self.loadoutPanel.destroy()
            self.loadoutPanel = None

    # ------------------------------------------------------------------
    # Death
    # ------------------------------------------------------------------
    def __handleLocalToonDied(self):
        street = self.street
        if street is None or not hasattr(street, 'fsm'):
            return
        current = street.fsm.getCurrentState().getName()
        if current in ('died', 'teleportOut', 'quietZone', 'final', 'tunnelOut'):
            return
        if self.endMode is not None:
            return
        if self.buildingActive:
            # Inside a building the interior place FSM owns the death; drive
            # its classic 'died' flow back to the playground and still show
            # the run report over it.
            place = base.cr.playGame.getPlace()
            if place is None or not hasattr(place, 'fsm') or place is street:
                return
            hoodId = street.loader.hood.id
            requestStatus = {'loader': ZoneUtil.getLoaderName(hoodId),
                             'where': ZoneUtil.getWhereName(hoodId, 1),
                             'how': 'teleportIn',
                             'hoodId': hoodId,
                             'zoneId': hoodId,
                             'shardId': None,
                             'avId': -1}
            self.endMode = 'gameover'
            place.fsm.request('died', [requestStatus])
            self.closeTierSelect()
            self.closeLoadout()
            self._showEndPanel(escaped=False)
            return
        self.endMode = 'gameover'
        hoodId = street.loader.hood.id
        requestStatus = {'loader': ZoneUtil.getLoaderName(hoodId),
                         'where': ZoneUtil.getWhereName(hoodId, 1),
                         'how': 'teleportIn',
                         'hoodId': hoodId,
                         'zoneId': hoodId,
                         'shardId': None,
                         'avId': -1}
        self.closeTierSelect()
        self.closeLoadout()
        self._showEndPanel(escaped=False,
                           doneCallback=lambda: self.__continueDeath(requestStatus))

    def __handleBuildingToonDied(self):
        """Death while detached inside a cog building interior."""
        if self.endMode is not None:
            return
        hoodId = self._buildingHoodId
        if hoodId is None:
            return
        place = base.cr.playGame.getPlace()
        if place is None or not hasattr(place, 'fsm'):
            return
        self.endMode = 'gameover'
        requestStatus = {'loader': ZoneUtil.getLoaderName(hoodId),
                         'where': ZoneUtil.getWhereName(hoodId, 1),
                         'how': 'teleportIn',
                         'hoodId': hoodId,
                         'zoneId': hoodId,
                         'shardId': None,
                         'avId': -1}
        place.fsm.request('died', [requestStatus])
        self.closeTierSelect()
        self.closeLoadout()
        self._showEndPanel(escaped=False)

    def __continueDeath(self, requestStatus):
        street = self.street
        if street is None or not hasattr(street, 'fsm'):
            return
        if street.fsm.getCurrentState().getName() not in ('died', 'teleportOut', 'quietZone', 'final', 'tunnelOut'):
            street.fsm.request('died', [requestStatus])

    def _showEndPanel(self, escaped, doneCallback=None):
        if self.hud is None:
            return
        existing = getattr(base, 'actionRunEndPanel', None)
        if existing is not None:
            existing.destroy()
        self.endMode = 'escaped' if escaped else 'gameover'
        base.actionRunEndPanel = RunEndPanel(self.streetName, self.hud.getRunSummary(), escaped, doneCallback)
