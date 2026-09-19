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
        self.buildingActive = False
        self.streetTransitionPending = False
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
        planner = self.getPlanner()
        if planner is not None:
            self.__primeHudFromPlanner(planner)

    def exit(self, force=False):
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
            # Extraction reports live above place teardown, so the player gets
            # to read what the run actually paid before the next zone loads.
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
        self.ignoreAll()
        self._stopPlannerWait()
        self.closeTierSelect()
        self.closeLoadout()
        # Keep the HUD, tier, and runStarted flag alive.  The destination
        # street reattaches this same object after its planner generates.

    def destroy(self):
        # A preserved building/connector run is intentionally detached from
        # its old Street.  If that Street is later being destroyed for a
        # real exit, force the teardown so an already-detached run cannot
        # remain registered forever.
        self.exit(force=True)
        if getattr(base, 'actionStreetRun', None) is self:
            base.actionStreetRun = None
        self.street = None

    def beginBuilding(self):
        self.buildingActive = True

    def cancelBuilding(self):
        self.buildingActive = False

    def returnToStreet(self, street):
        continueRun = self.streetTransitionPending or self.buildingActive
        self.street = street
        self.branchZone = street.loader.branchZone
        self.canonicalBranch = ZoneUtil.getCanonicalBranchZone(street.loader.zoneId)
        self.streetName = self._lookupStreetName()
        self.buildingActive = False
        self.streetTransitionPending = False
        self.entered = True
        self.accept(base.localAvatar.uniqueName('died'), self.__handleLocalToonDied)
        self.accept('action-planner-ready', self.__handlePlannerReady)
        planner = self.getPlanner()
        if continueRun:
            # A new street has a new distributed planner.  Re-register the
            # preserved run there without reopening tier selection.
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
