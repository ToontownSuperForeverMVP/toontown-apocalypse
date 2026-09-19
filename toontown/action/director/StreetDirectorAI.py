"""Per-street combat director (AI side).

One director lives on every :class:`DistributedSuitPlannerAI`.  The legacy
planner keeps answering the low-level question ("where can a Cog physically
spawn and walk?"); the director answers "what should spawn here right now and
what does it pay?".

Responsibilities:

* track Toons that started a street run (and the tier they chose)
* own the rolling :class:`PressureMeter` and the :class:`DifficultyProfile`
* spawn Cogs on a cadence and at levels derived from the profile
* tick every :class:`CogCombatControllerAI`
* hand out engage slots so a Toon is never mobbed beyond the profile limit
* validate gag hits, award mastery XP and jellybeans, broadcast kills
* run the procedural contract (objectives) for the street
* manage deployable traps, self toon-ups and gag-track caches
"""

import random

from panda3d.core import Point3, Vec3
from direct.directnotify import DirectNotifyGlobal
from direct.showbase.DirectObject import DirectObject
from direct.task import Task

from toontown.action import ActionGlobals, ActionProgression
from toontown.action.cog.CogCombatControllerAI import CogCombatControllerAI
from toontown.action.director.PressureDirector import PressureMeter
from toontown.action.objectives import ObjectiveGenerator
from toontown.hood import ZoneUtil
from toontown.suit import SuitDNA


class StreetDirectorAI(DirectObject):
    notify = DirectNotifyGlobal.directNotify.newCategory('StreetDirectorAI')

    TICK_INTERVAL = 0.1
    TOON_VALIDATE_INTERVAL = 1.0
    PROFILE_REFRESH_INTERVAL = 1.0
    NETWORK_SYNC_INTERVAL = 1.0
    # A run survives the Toon stepping into a shop for this long.
    RUN_EMPTY_GRACE = 25.0
    # A connector can unload the old street before the destination planner
    # receives the resume request.  Keep the rolling run state in memory
    # long enough to cover that distributed handoff.
    RUN_HANDOFF_GRACE = 45.0
    # A Toon stuck in a doorway is considered for spawning an objective dept.
    OBJECTIVE_DEPT_BIAS = 0.35
    # How far a freshly spawned cache should be from every active Toon.
    CACHE_MIN_TOON_DISTANCE = 40.0
    # Fallback street levels when a planner has no hood info.
    DEFAULT_BASE_LEVELS = (1, 2, 3)

    def __init__(self, planner):
        DirectObject.__init__(self)
        self.planner = planner
        self.air = planner.air
        self.zoneId = planner.zoneId
        self.rng = random.Random()
        self.tier = ActionGlobals.DEFAULT_TIER
        self.pressure = PressureMeter(self.tier)
        self.profile = ActionGlobals.getDifficultyProfile(self.tier, ActionGlobals.MIN_POWER, 0)
        self.activeToons = {}
        # Toons using a street restock clerk are temporarily outside combat.
        # Keep this in the director as well as on the avatar so an attack that
        # was already targeting them cannot resolve during the purchase.
        self.safeToons = set()
        self.toonSamples = {}
        self.controllers = {}
        self.engageSlots = {}
        self.lastAttackOnToon = {}
        self.volleys = {}
        self.volleyTargets = {}
        self.objectives = []
        self.objectivesDirty = False
        self.kills = 0
        self.nextBreakthroughKill = ActionGlobals.BREAKTHROUGH_KILLS
        self.killChains = {}
        self.killsSinceCache = 0
        self.cache = None
        self.traps = {}
        self.nextTrapId = 1
        self.toonUpReady = {}
        self.runActive = False
        self.emptySince = None
        self.handoffUntil = 0.0
        self.lastTickTime = None
        self.lastToonValidate = 0.0
        self.lastProfileRefresh = 0.0
        self.lastNetworkSync = 0.0
        self.lastSentPressure = -1
        self.lastSpawnTime = -1000.0
        # Resolved lazily in start(): the planner has no doId until it has
        # been generated, and we are constructed from its __init__.
        self.taskName = None
        self.started = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self):
        if self.started:
            return
        self.started = True
        self.lastTickTime = globalClock.getFrameTime()
        planner = self.planner
        doId = getattr(planner, 'doId', None) if planner is not None else None
        self.taskName = 'actionDirector-%s' % (doId if doId is not None else id(self))
        taskMgr.add(self.__tick, self.taskName)
        self.d_setTier()
        self.d_setPressure(force=True)
        self.d_setObjectives()

    def stop(self):
        self.started = False
        if self.taskName is not None:
            taskMgr.remove(self.taskName)
            self.taskName = None
        self.ignoreAll()
        for controller in list(self.controllers.values()):
            controller.cleanup()
        self.controllers = {}
        self.engageSlots = {}
        self._removeCache()
        self.traps = {}
        self.activeToons = {}
        self.safeToons = set()
        self.killChains.clear()
        self.planner = None

    # ------------------------------------------------------------------
    # Toons entering / leaving
    # ------------------------------------------------------------------
    def requestStreetRun(self, avId, tier):
        toon = self.air.doId2do.get(avId)
        if toon is None:
            return
        tier = max(ActionGlobals.MIN_TIER, min(ActionGlobals.MAX_TIER, int(tier)))
        now = globalClock.getFrameTime()
        handoff = self._takeRunHandoff(avId, now)
        if handoff is not None:
            self._resumeRun(avId, toon, handoff, now)
            return
        self._ensureStarterKit(toon)
        othersPresent = any(otherId != avId for otherId in self.activeToons)
        self.activeToons[avId] = now
        self.toonSamples[avId] = [Point3(toon.getPos()), now, Vec3(0, 0, 0)]
        self.emptySince = None
        self.accept(toon.getGoneSadMessage(), self.__handleToonDied, [avId])
        if not othersPresent and tier != self.tier:
            self.setTier(tier)
        if not self.runActive:
            self._startRun(now)
        else:
            self._refreshProfile(now, force=True)
        self.notify.info('Toon %d started a run on %d at tier %d' % (avId, self.zoneId, self.tier))

    def leaveStreetRun(self, avId):
        self._dropToon(avId)

    def _dropToon(self, avId):
        if avId not in self.activeToons:
            return
        del self.activeToons[avId]
        self.safeToons.discard(avId)
        self.toonSamples.pop(avId, None)
        self.lastAttackOnToon.pop(avId, None)
        self.killChains.pop(avId, None)
        toon = self.air.doId2do.get(avId)
        if toon is not None:
            self.ignore(toon.getGoneSadMessage())
        controllers = self.engageSlots.pop(avId, set())
        for controller in controllers:
            if controller.getTargetId() == avId:
                controller.targetId = 0
        if not self.activeToons:
            self.emptySince = globalClock.getFrameTime()

    def _ensureStarterKit(self, toon):
        tiers = ActionProgression.getTrackTiers(toon)
        if not any(tier > 0 for tier in tiers):
            access = list(toon.getTrackAccess() or [0] * ActionGlobals.NUM_TRACKS)
            while len(access) < ActionGlobals.NUM_TRACKS:
                access.append(0)
            for track in ActionGlobals.STARTER_TRACKS:
                access[track] = max(access[track], 1)
            toon.b_setTrackAccess(access)
        if not ActionProgression.getEquippedTracks(toon) and hasattr(toon, 'b_setEquippedTracks'):
            toon.b_setEquippedTracks(ActionProgression.getDefaultLoadout(toon))
        if toon.getMaxMoney() < ActionGlobals.MAX_JELLYBEANS and hasattr(toon, 'b_setMaxMoney'):
            toon.b_setMaxMoney(ActionGlobals.MAX_JELLYBEANS)

    def __handleToonDied(self, avId):
        if avId not in self.activeToons:
            return
        if self.pressure.onToonDied():
            self._onStageChanged()
        self.d_setPressure(force=True)
        self.planner.sendUpdate('actionAnnounce', [ActionGlobals.ANNOUNCE_TOON_DOWN, avId, 0])
        self._dropToon(avId)

    def _validateToons(self, now):
        for avId in list(self.activeToons.keys()):
            toon = self.air.doId2do.get(avId)
            if toon is None:
                self._dropToon(avId)
                continue
            zoneId = getattr(toon, 'zoneId', None)
            if zoneId is None:
                self._dropToon(avId)
                continue
            if ZoneUtil.getBranchZone(zoneId) != self.zoneId:
                where = ZoneUtil.getToonWhereName(zoneId)
                if self.runActive and where in ('street', 'suitInterior'):
                    self._queueRunHandoff(avId, now)
                else:
                    self._dropToon(avId)

    def _runHandoffState(self, avId, now):
        return {
            'avId': avId,
            'createdAt': now,
            'tier': self.tier,
            'pressure': self.pressure.getValue(),
            'peakPressure': int(self.pressure.peakValue),
            'kills': self.kills,
            'nextBreakthroughKill': self.nextBreakthroughKill,
            'killsSinceCache': self.killsSinceCache,
            'objectives': [objective.toWire() for objective in self.objectives],
        }

    def _queueRunHandoff(self, avId, now=None):
        if not self.runActive:
            self._dropToon(avId)
            return
        if now is None:
            now = globalClock.getFrameTime()
        handoffs = getattr(self.air, 'actionRunHandoffs', None)
        if handoffs is None:
            handoffs = {}
            self.air.actionRunHandoffs = handoffs
        handoffs[avId] = self._runHandoffState(avId, now)
        self._dropToon(avId)
        self.handoffUntil = max(self.handoffUntil, now + self.RUN_HANDOFF_GRACE)

    def _exportRunHandoff(self, avId, now=None):
        if not self.runActive or avId not in self.activeToons:
            return None
        if now is None:
            now = globalClock.getFrameTime()
        state = self._runHandoffState(avId, now)
        self._dropToon(avId)
        self.handoffUntil = max(self.handoffUntil, now + self.RUN_HANDOFF_GRACE)
        return state

    def _takeRunHandoff(self, avId, now):
        handoffs = getattr(self.air, 'actionRunHandoffs', None)
        if handoffs is not None:
            state = handoffs.pop(avId, None)
            if state is not None:
                if now - state.get('createdAt', now) <= self.RUN_HANDOFF_GRACE:
                    return state

        # The connector may arrive before the old director's validation tick.
        for planner in getattr(self.air, 'suitPlanners', {}).values():
            director = getattr(planner, 'actionDirector', None)
            if director is None or director is self:
                continue
            state = director._exportRunHandoff(avId, now)
            if state is not None:
                return state
        return None

    def _resumeRun(self, avId, toon, state, now):
        self._ensureStarterKit(toon)
        self.tier = max(ActionGlobals.MIN_TIER,
                        min(ActionGlobals.MAX_TIER, int(state.get('tier', self.tier))))
        self.pressure.setTier(self.tier)
        self.pressure.setValue(state.get('pressure', 0))
        self.pressure.peakValue = max(self.pressure.peakValue,
                                      float(state.get('peakPressure', state.get('pressure', 0))))
        self.kills = int(state.get('kills', 0))
        self.nextBreakthroughKill = max(
            ActionGlobals.BREAKTHROUGH_KILLS,
            int(state.get('nextBreakthroughKill', ActionGlobals.BREAKTHROUGH_KILLS)))
        self.killsSinceCache = int(state.get('killsSinceCache', 0))
        self.objectives = [ObjectiveGenerator.Objective.fromWire(item)
                           for item in state.get('objectives', [])]
        self.objectivesDirty = False
        self.runActive = True
        self.emptySince = None
        self.activeToons[avId] = now
        self.toonSamples[avId] = [Point3(toon.getPos()), now, Vec3(0, 0, 0)]
        self.accept(toon.getGoneSadMessage(), self.__handleToonDied, [avId])
        self._refreshProfile(now, force=True)
        self.d_setTier()
        self.d_setPressure(force=True)
        self.d_setObjectives()
        self.notify.info('Toon %d resumed a street run on %d at tier %d with pressure %d' %
                         (avId, self.zoneId, self.tier, self.pressure.getValue()))

    def _sampleToons(self, now):
        for avId in self.activeToons:
            toon = self.air.doId2do.get(avId)
            if toon is None:
                continue
            sample = self.toonSamples.get(avId)
            pos = Point3(toon.getPos())
            if sample is None:
                self.toonSamples[avId] = [pos, now, Vec3(0, 0, 0)]
                continue
            lastPos, lastTime, velocity = sample
            dt = now - lastTime
            if dt >= 0.2:
                newVelocity = (pos - lastPos) / dt
                # Toons that teleport produce absurd velocities; clamp them.
                if newVelocity.length() > 40.0:
                    newVelocity = Vec3(0, 0, 0)
                sample[0] = pos
                sample[1] = now
                sample[2] = newVelocity * 0.6 + velocity * 0.4

    # ------------------------------------------------------------------
    # Queries used by the Cog controllers
    # ------------------------------------------------------------------
    def isRunActive(self):
        return self.runActive and bool(self.activeToons)

    def getActiveToon(self, avId):
        if avId not in self.activeToons or avId in self.safeToons:
            return None
        toon = self.air.doId2do.get(avId)
        if toon is None or getattr(toon, 'actionSafe', False):
            return None
        return toon

    def getEngageableToons(self):
        toons = []
        for avId in self.activeToons:
            if avId in self.safeToons:
                continue
            toon = self.air.doId2do.get(avId)
            if (toon is not None and not getattr(toon, 'actionSafe', False)
                    and getattr(toon, 'hp', 0) > 0):
                toons.append(toon)
        return toons

    def setToonSafe(self, avId, safe=True):
        """Pause street combat for a Toon during a clerk interaction."""
        if safe:
            self.safeToons.add(avId)
            # Cancel an in-flight telegraph as well as its target.  Otherwise
            # the wind-up could still call takeDamage after the Toon heals.
            for controller in list(self.controllers.values()):
                if controller.getTargetId() == avId:
                    controller.currentAttack = None
                    controller.targetId = 0
                    controller.lostTargetSince = None
                    self.releaseEngageSlot(controller)
        else:
            self.safeToons.discard(avId)

    def getToonVelocity(self, avId):
        sample = self.toonSamples.get(avId)
        if sample is None:
            return Vec3(0, 0, 0)
        return Vec3(sample[2])

    def getEngagedControllers(self):
        return [controller for controller in self.controllers.values() if controller.isEngaged()]

    def requestEngageSlot(self, avId, controller, force=False):
        if avId not in self.activeToons or avId in self.safeToons:
            return False
        slots = self.engageSlots.setdefault(avId, set())
        if controller in slots:
            return True
        if not force and len(slots) >= self.profile.engageLimit:
            return False
        self.releaseEngageSlot(controller)
        slots.add(controller)
        return True

    def releaseEngageSlot(self, controller):
        for slots in self.engageSlots.values():
            slots.discard(controller)

    def canAttackToon(self, avId, now):
        if avId in self.safeToons:
            return False
        gap = ActionGlobals.TOON_INCOMING_ATTACK_GAP / max(1.0, self.profile.aggression)
        return now - self.lastAttackOnToon.get(avId, -100.0) >= gap

    def noteAttackStart(self, avId, now):
        self.lastAttackOnToon[avId] = now

    def getProfile(self):
        return self.profile

    def getTier(self):
        return self.tier

    # ------------------------------------------------------------------
    # Suit registration
    # ------------------------------------------------------------------
    def registerSuit(self, suit):
        if suit.doId in self.controllers:
            return self.controllers[suit.doId]
        try:
            controller = CogCombatControllerAI(suit, self)
        except Exception:
            self.notify.warning('Could not build a combat controller for suit %s' % suit.doId)
            return None
        self.controllers[suit.doId] = controller
        suit.actionController = controller
        return controller

    def unregisterSuit(self, suit):
        controller = self.controllers.pop(suit.doId, None)
        if controller is not None:
            self.releaseEngageSlot(controller)
            controller.cleanup()
        suit.actionController = None

    # ------------------------------------------------------------------
    # Run / tier / profile
    # ------------------------------------------------------------------
    def setTier(self, tier):
        self.tier = max(ActionGlobals.MIN_TIER, min(ActionGlobals.MAX_TIER, int(tier)))
        self.pressure.setTier(self.tier)
        self._refreshProfile(globalClock.getFrameTime(), force=True)
        self.d_setTier()

    def _startRun(self, now):
        self.runActive = True
        self.emptySince = None
        self.pressure.reset()
        self.kills = 0
        self.nextBreakthroughKill = ActionGlobals.BREAKTHROUGH_KILLS
        self.killsSinceCache = 0
        self.volleys = {}
        self.volleyTargets = {}
        self._refreshProfile(now, force=True)
        self._generateObjectives()
        self.d_setPressure(force=True)
        self.planner.sendUpdate('actionAnnounce', [ActionGlobals.ANNOUNCE_RUN_STARTED, 0, self.tier])
        self._maybeSpawnCache(initial=True)
        # Spawn the first directed Cog quickly so the street feels alive.
        self.lastSpawnTime = now - self.profile.spawnInterval + 2.0

    def _endRun(self):
        self.runActive = False
        self.killChains.clear()
        self.emptySince = None
        self.handoffUntil = 0.0
        self.engageSlots = {}
        self.safeToons = set()
        self.objectives = []
        self.d_setObjectives()
        for trapId in list(self.traps.keys()):
            self._removeTrap(trapId, 0)
        self._removeCache()

    def _refreshProfile(self, now, force=False):
        if not force and now - self.lastProfileRefresh < self.PROFILE_REFRESH_INTERVAL:
            return
        self.lastProfileRefresh = now
        powers = []
        for avId in self.activeToons:
            toon = self.air.doId2do.get(avId)
            if toon is not None:
                powers.append(ActionProgression.getPowerRating(toon))
        power = sum(powers) / len(powers) if powers else ActionGlobals.MIN_POWER
        self.profile = ActionGlobals.getDifficultyProfile(self.tier, power, self.pressure.value)

    def _onStageChanged(self):
        self.d_setPressure(force=True)
        self._progressObjectives(stage=self.pressure.getStage())

    def _getHoodInfo(self):
        planner = self.planner
        if planner is None or planner.hoodInfoIdx < 0:
            return None
        return planner.SuitHoodInfo[planner.hoodInfoIdx]

    def getBaseLevels(self):
        hoodInfo = self._getHoodInfo()
        if hoodInfo is None:
            return self.DEFAULT_BASE_LEVELS
        return tuple(hoodInfo[self.planner.SUIT_HOOD_INFO_LVL]) or self.DEFAULT_BASE_LEVELS

    def getDeptWeights(self):
        hoodInfo = self._getHoodInfo()
        if hoodInfo is None:
            return (25, 25, 25, 25)
        return tuple(hoodInfo[self.planner.SUIT_HOOD_INFO_TRACK])

    def getPopulationTarget(self):
        hoodInfo = self._getHoodInfo()
        base = hoodInfo[self.planner.SUIT_HOOD_INFO_MIN] if hoodInfo is not None else 3
        return min(self.planner.TOTAL_MAX_SUITS, base + self.profile.populationBonus)

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------
    def __tick(self, task):
        if self.planner is None:
            return Task.done
        # One bad frame must never kill the street's director for good: an
        # uncaught exception would remove this task permanently.
        try:
            self._tickBody()
        except Exception:
            self.notify.warning('Street director tick failed in zone %s' % self.zoneId)
            import traceback
            traceback.print_exc()
        return Task.cont

    def _tickBody(self):
        now = globalClock.getFrameTime()
        dt = 0.0 if self.lastTickTime is None else max(0.0, min(0.5, now - self.lastTickTime))
        self.lastTickTime = now

        if now - self.lastToonValidate >= self.TOON_VALIDATE_INTERVAL:
            self.lastToonValidate = now
            self._validateToons(now)

        toonsPresent = bool(self.activeToons)
        if toonsPresent:
            self.emptySince = None
        elif self.runActive:
            if self.emptySince is None:
                self.emptySince = now
            elif now >= self.handoffUntil and now - self.emptySince > self.RUN_EMPTY_GRACE:
                self._endRun()

        # Do not let the old street's empty-state decay erase the rolling
        # difficulty while a connector is handing the run to another street.
        pressureChanged = False
        if toonsPresent or now >= self.handoffUntil:
            pressureChanged = self.pressure.tick(dt, toonsPresent and self.runActive)
        if pressureChanged:
            self._onStageChanged()
        self._refreshProfile(now)
        self._sampleToons(now)

        if self.runActive and toonsPresent:
            self._tickSpawning(now)

        for controller in list(self.controllers.values()):
            try:
                controller.tick(now, dt, self.profile)
            except Exception:
                self.notify.warning('Combat controller tick failed; removing controller')
                import traceback
                traceback.print_exc()
                suit = controller.suit
                if suit is not None:
                    self.unregisterSuit(suit)

        self._tickTraps(now)

        if now - self.lastNetworkSync >= self.NETWORK_SYNC_INTERVAL:
            self.lastNetworkSync = now
            self.d_setPressure()
            if self.objectivesDirty:
                self.d_setObjectives()

    # ------------------------------------------------------------------
    # Spawning
    # ------------------------------------------------------------------
    def _tickSpawning(self, now):
        if now - self.lastSpawnTime < self.profile.spawnInterval:
            return
        self.lastSpawnTime = now
        if len(self.planner.suitList) >= self.getPopulationTarget():
            return
        self._spawnDirectedSuit()

    def _pickSpawnDept(self):
        for objective in self.objectives:
            if objective.kind == ActionGlobals.OBJ_DEFEAT_DEPT and not objective.complete:
                if self.rng.random() < self.OBJECTIVE_DEPT_BIAS:
                    return ActionGlobals.DEPT_CODES[objective.param]
        return None

    def _spawnDirectedSuit(self):
        level = ActionGlobals.pickSpawnLevel(self.profile, self.getBaseLevels(), self.rng)
        elite = self.rng.random() < self.profile.eliteChance
        if elite:
            level = min(ActionGlobals.MAX_COG_LEVEL, level + ActionGlobals.ELITE_LEVEL_BONUS)
        dept = self._pickSpawnDept()
        try:
            suit = self.planner.createNewSuit([], self.planner.streetPointList[:], suitLevel=level,
                                              suitTrack=dept, skelecog=1 if elite else None)
        except Exception:
            self.notify.warning('Directed spawn failed on %d' % self.zoneId)
            import traceback
            traceback.print_exc()
            return None
        if suit is not None and elite:
            self.planner.sendUpdate('actionAnnounce', [ActionGlobals.ANNOUNCE_ELITE_SPAWNED, 0, level])
        return suit

    # ------------------------------------------------------------------
    # Gag hits, kills and rewards
    # ------------------------------------------------------------------
    def validateGagHit(self, suit, toon, gagDef, now):
        """Cooldown / volley check shared by every Cog on the street."""
        key = (toon.doId, gagDef.track)
        last = self.volleys.get(key)
        if last is None or now - last >= gagDef.cooldown * 0.8:
            self.volleys[key] = now
            self.volleyTargets[key] = {suit.doId}
            return True
        targets = self.volleyTargets.setdefault(key, set())
        multiTarget = gagDef.splash > 0.0 or gagDef.style in (ActionGlobals.GAG_STYLE_RADIAL,
                                                               ActionGlobals.GAG_STYLE_DROP)
        if multiTarget and now - last <= ActionGlobals.GAG_VOLLEY_WINDOW and suit.doId not in targets:
            targets.add(suit.doId)
            return True
        return False

    def getStatusForSuit(self, suit, now):
        controller = self.controllers.get(suit.doId)
        if controller is None:
            return False, False
        return controller.isLured(now), controller.isSoaked(now)

    def onGagHit(self, suit, toon, gagDef, damage, now):
        if gagDef.damage > 0 and damage > 0:
            xp = ActionGlobals.getHitXp(damage, suit.getActualLevel(), self.profile)
            self._grantXp(toon, gagDef.track, xp)
        controller = self.controllers.get(suit.doId)
        if controller is not None:
            controller.onDamaged(toon, gagDef, damage, now)
        if damage > 0:
            self._progressObjectives(hitTrack=gagDef.track)

    def onLure(self, suit, toon, gagDef, now):
        controller = self.controllers.get(suit.doId)
        if controller is None:
            return
        controller.applyLure(toon, gagDef, now)
        xp = max(1, int(round(4 * self.profile.xpScale)))
        self._grantXp(toon, ActionGlobals.LURE_TRACK, xp)
        self._progressObjectives(hitTrack=ActionGlobals.LURE_TRACK)

    def onCogDefeated(self, suit, toon, gagDef):
        level = suit.getActualLevel()
        elite = bool(suit.getSkelecog())
        beans = ActionGlobals.getKillBeans(level, self.profile, elite)
        xp = ActionGlobals.getKillXp(level, self.profile, elite)
        track = gagDef.track if gagDef is not None else ActionGlobals.THROW_TRACK
        if toon is not None:
            now = globalClock.getFrameTime()
            count, lastKill = self.killChains.get(toon.doId, (0, -float('inf')))
            count = count + 1 if now - lastKill <= ActionGlobals.KILL_CHAIN_WINDOW else 1
            self.killChains[toon.doId] = (count, now)
            bonus = min(ActionGlobals.KILL_CHAIN_MAX_BONUS,
                        (count - 1) * ActionGlobals.KILL_CHAIN_BONUS_STEP)
            beans = int(round(beans * (1.0 + bonus)))
            xp = int(round(xp * (1.0 + bonus)))
            self.planner.sendUpdate('actionAnnounce',
                                    [ActionGlobals.ANNOUNCE_KILL_CHAIN, toon.doId, min(count, 65535)])
            self._grantBeans(toon, beans)
            self._grantXp(toon, track, xp)
            toonId = toon.doId
        else:
            toonId = 0
        suit.sendUpdate('actionDefeated', [toonId, beans, track, xp, int(elite)])
        controller = self.controllers.get(suit.doId)
        if controller is not None:
            controller.onDefeated()
        self.kills += 1
        self.killsSinceCache += 1
        if self.pressure.onCogDefeated(level, elite):
            self._onStageChanged()
        dept = None
        try:
            dept = SuitDNA.getSuitDept(suit.dna.name)
        except Exception:
            pass
        self._progressObjectives(kill=True, dept=dept, level=level, elite=elite)
        if toon is not None:
            self._progressObjectives(chain=self.killChains.get(toon.doId, (0, 0))[0])
        self._checkBreakthrough()
        self._maybeSpawnCache()

    def onAttackResolved(self, controller, toon, realtime, damage):
        if damage > 0:
            self.killChains.pop(toon.doId, None)
        if damage <= 0:
            if self.pressure.onDodge():
                self._onStageChanged()
            self._progressObjectives(dodge=True)

    def _grantXp(self, toon, track, xp):
        if xp <= 0 or track < 0 or track >= ActionGlobals.NUM_TRACKS:
            return
        experience = getattr(toon, 'experience', None)
        if experience is None:
            return
        if ActionProgression.getTrackTier(toon, track) <= 0:
            return
        before = experience.getExp(track)
        experience.addExp(track, xp)
        if experience.getExp(track) != before:
            toon.d_setExperience(experience.getCurrentExperience())

    def _grantBeans(self, toon, beans):
        if beans <= 0:
            return
        toon.addMoney(beans)

    # ------------------------------------------------------------------
    # Objectives
    # ------------------------------------------------------------------
    def _collectEquippedTracks(self):
        tracks = set()
        for avId in self.activeToons:
            toon = self.air.doId2do.get(avId)
            if toon is not None:
                tracks.update(ActionProgression.getEquippedTracks(toon))
        return sorted(tracks) or list(ActionGlobals.STARTER_TRACKS)

    def _anyUndiscoveredTracks(self):
        for avId in self.activeToons:
            toon = self.air.doId2do.get(avId)
            if toon is not None and ActionProgression.getUndiscoveredTracks(toon):
                return True
        return False

    def _generateObjectives(self):
        generator = ObjectiveGenerator.ObjectiveGenerator(self.tier, self.getBaseLevels(), self.getDeptWeights(),
                                                          self._collectEquippedTracks(), rng=self.rng)
        self.objectives = generator.generate(allowCache=self._anyUndiscoveredTracks())
        self.d_setObjectives()

    def _progressObjectives(self, kill=False, dept=None, level=0, elite=False, dodge=False,
                            hitTrack=None, cache=False, stage=None, chain=0):
        if not self.runActive or not self.objectives:
            return
        completed = []
        for index, objective in enumerate(self.objectives):
            if objective.complete:
                continue
            before = objective.progress
            done = False
            kind = objective.kind
            if kill and kind == ActionGlobals.OBJ_DEFEAT_ANY:
                done = objective.addProgress()
            elif kill and kind == ActionGlobals.OBJ_DEFEAT_DEPT and dept is not None \
                    and objective.param < len(ActionGlobals.DEPT_CODES) \
                    and dept == ActionGlobals.DEPT_CODES[objective.param]:
                done = objective.addProgress()
            elif kill and kind == ActionGlobals.OBJ_DEFEAT_LEVEL and level >= objective.param:
                done = objective.addProgress()
            elif kill and elite and kind == ActionGlobals.OBJ_DEFEAT_ELITE:
                done = objective.addProgress()
            elif dodge and kind == ActionGlobals.OBJ_DODGE:
                done = objective.addProgress()
            elif hitTrack is not None and kind == ActionGlobals.OBJ_HITS_WITH_TRACK and objective.param == hitTrack:
                done = objective.addProgress()
            elif cache and kind == ActionGlobals.OBJ_FIND_CACHE:
                done = objective.addProgress()
            elif stage is not None and kind == ActionGlobals.OBJ_REACH_STAGE and stage >= objective.param:
                done = objective.setProgress(1)
            elif chain > 0 and kind == ActionGlobals.OBJ_KILL_CHAIN:
                done = objective.setProgress(chain)
            if objective.progress != before:
                self.objectivesDirty = True
            if done:
                completed.append((index, objective))
        for index, objective in completed:
            self._completeObjective(index, objective)
        if completed:
            self.d_setObjectives()

    def _completeObjective(self, index, objective):
        stageMult = ActionGlobals.PRESSURE_STAGE_REWARD_MULT[self.pressure.getStage()]
        beans = max(1, int(round(objective.beans * stageMult)))
        xp = ObjectiveGenerator.objectiveXp(objective, self.tier)
        for avId in list(self.activeToons.keys()):
            toon = self.air.doId2do.get(avId)
            if toon is None:
                continue
            equipped = ActionProgression.getEquippedTracks(toon)
            track = self.rng.choice(equipped) if equipped else ActionGlobals.THROW_TRACK
            self._grantBeans(toon, beans)
            self._grantXp(toon, track, xp)
            self.planner.sendUpdateToAvatarId(avId, 'actionObjectiveComplete', [index, beans, track, xp])
        if self.pressure.onObjectiveComplete():
            self._onStageChanged()
        if all(item.complete for item in self.objectives):
            bonus = ObjectiveGenerator.contractClearedBeans(self.tier, self.profile.rewardScale)
            for avId in list(self.activeToons.keys()):
                toon = self.air.doId2do.get(avId)
                if toon is not None:
                    self._grantBeans(toon, bonus)
            self.planner.sendUpdate('actionAnnounce', [ActionGlobals.ANNOUNCE_CONTRACT_CLEARED, 0, min(65535, bonus)])
            self._generateObjectives()

    def _checkBreakthrough(self):
        """Reward a long shared run and shave off enough pressure to reset tempo."""
        if self.kills < self.nextBreakthroughKill:
            return
        while self.kills >= self.nextBreakthroughKill:
            self.nextBreakthroughKill += ActionGlobals.BREAKTHROUGH_KILLS
        reward = int(round(ActionGlobals.BREAKTHROUGH_BEANS_BASE * self.profile.rewardScale))
        for avId in list(self.activeToons):
            toon = self.air.doId2do.get(avId)
            if toon is not None:
                self._grantBeans(toon, reward)
        self.pressure.addRaw(-ActionGlobals.BREAKTHROUGH_PRESSURE_RELIEF)
        self._refreshProfile(globalClock.getFrameTime(), force=True)
        self.d_setPressure(force=True)
        self.planner.sendUpdate('actionAnnounce', [ActionGlobals.ANNOUNCE_BREAKTHROUGH, 0, reward])

    # ------------------------------------------------------------------
    # Traps
    # ------------------------------------------------------------------
    def requestTrap(self, avId, level, x, y, z):
        toon = self.getActiveToon(avId)
        if toon is None or getattr(toon, 'hp', 0) <= 0:
            return
        gagDef = ActionGlobals.getGagDef(ActionGlobals.TRAP_TRACK, level)
        if gagDef is None or not ActionProgression.canUseGag(toon, ActionGlobals.TRAP_TRACK, level):
            return
        pos = Point3(x, y, z)
        if (pos - Point3(toon.getPos())).length() > gagDef.range + ActionGlobals.GAG_RANGE_SLACK:
            return
        now = globalClock.getFrameTime()
        key = (avId, ActionGlobals.TRAP_TRACK)
        if now - self.volleys.get(key, -100.0) < gagDef.cooldown * 0.8:
            return
        self.volleys[key] = now
        trapId = self.nextTrapId
        self.nextTrapId = (self.nextTrapId % 60000) + 1
        self.traps[trapId] = {'pos': pos, 'gagDef': gagDef, 'avId': avId, 'expires': now + gagDef.duration}
        self.planner.sendUpdate('actionTrapPlaced', [trapId, avId, level, x, y, z])

    def _tickTraps(self, now):
        if not self.traps:
            return
        for trapId, trap in list(self.traps.items()):
            if now >= trap['expires']:
                self._removeTrap(trapId, 0)
                continue
            gagDef = trap['gagDef']
            triggerRadius = max(1.5, gagDef.splash * 0.6)
            for controller in list(self.controllers.values()):
                if not controller.isActive() or not controller.havePos:
                    continue
                if ActionGlobals.flatDistance(controller.pos, trap['pos']) <= triggerRadius:
                    self._triggerTrap(trapId, trap, controller, now)
                    break

    def _triggerTrap(self, trapId, trap, triggerController, now):
        gagDef = trap['gagDef']
        toon = self.air.doId2do.get(trap['avId'])
        victims = []
        for controller in list(self.controllers.values()):
            if not controller.isActive() or not controller.havePos:
                continue
            if ActionGlobals.flatDistance(controller.pos, trap['pos']) <= gagDef.splash + 0.5:
                victims.append(controller)
        self._removeTrap(trapId, triggerController.suit.doId if triggerController.suit else 0)
        for controller in victims:
            suit = controller.suit
            if suit is None:
                continue
            if toon is not None:
                damage = ActionProgression.getGagDamageForToon(toon, gagDef)
            else:
                damage = gagDef.damage
            suit.applyActionDamage(toon, gagDef, damage, now, validate=False)

    def _removeTrap(self, trapId, suitId):
        if trapId in self.traps:
            del self.traps[trapId]
            if self.planner is not None:
                self.planner.sendUpdate('actionTrapTriggered', [trapId, suitId])

    # ------------------------------------------------------------------
    # Toon-Up
    # ------------------------------------------------------------------
    def requestToonUp(self, avId, level):
        toon = self.getActiveToon(avId)
        if toon is None or getattr(toon, 'hp', 0) <= 0:
            return
        gagDef = ActionGlobals.getGagDef(ActionGlobals.HEAL_TRACK, level)
        if gagDef is None or not ActionProgression.canUseGag(toon, ActionGlobals.HEAL_TRACK, level):
            return
        now = globalClock.getFrameTime()
        if now < self.toonUpReady.get(avId, -100.0):
            return
        self.toonUpReady[avId] = now + gagDef.cooldown * 0.8
        mastery = ActionGlobals.getMasteryFraction(ActionProgression.getTrackXp(toon, ActionGlobals.HEAL_TRACK))
        amount = max(1, int(round(gagDef.heal * (1.0 + ActionGlobals.MASTERY_DAMAGE_BONUS * mastery))))
        before = toon.getHp()
        toon.toonUp(amount)
        healed = max(0, toon.getHp() - before)
        self.planner.sendUpdate('actionToonUp', [avId, level, healed])
        if healed > 0:
            self._grantXp(toon, ActionGlobals.HEAL_TRACK, max(1, int(round(healed * 0.5 * self.profile.xpScale))))

    # ------------------------------------------------------------------
    # Gag track caches
    # ------------------------------------------------------------------
    def _maybeSpawnCache(self, initial=False):
        if self.cache is not None or not self.runActive or not self.activeToons:
            return
        if not self._anyUndiscoveredTracks():
            return
        if initial:
            chance = ActionGlobals.CACHE_SPAWN_CHANCE_BASE + ActionGlobals.CACHE_SPAWN_CHANCE_PER_TIER * self.tier
        elif self.killsSinceCache >= ActionGlobals.CACHE_RESPAWN_KILLS:
            chance = ActionGlobals.CACHE_RESPAWN_CHANCE
            self.killsSinceCache = 0
        else:
            return
        if self.rng.random() >= chance:
            return
        spot = self._pickCacheSpot()
        if spot is None:
            return
        zoneId, pos = spot
        self._spawnCache(zoneId, pos)

    def _pickCacheSpot(self):
        planner = self.planner
        try:
            zoneMap = planner.getZoneIdToPointMap()
        except Exception:
            return None
        toonPositions = []
        for avId in self.activeToons:
            toon = self.air.doId2do.get(avId)
            if toon is not None:
                toonPositions.append(Point3(toon.getPos()))
        candidates = []
        for zoneId, points in zoneMap.items():
            for point in points:
                pos = Point3(point.getPos())
                if all((pos - toonPos).length() >= self.CACHE_MIN_TOON_DISTANCE for toonPos in toonPositions):
                    candidates.append((zoneId, pos))
        if not candidates:
            for zoneId, points in zoneMap.items():
                for point in points:
                    candidates.append((zoneId, Point3(point.getPos())))
        if not candidates:
            return None
        zoneId, pos = self.rng.choice(candidates)
        return ZoneUtil.getTrueZoneId(zoneId, self.zoneId), pos

    def _spawnCache(self, zoneId, pos):
        from toontown.action.DistributedTrackCacheAI import DistributedTrackCacheAI
        cache = DistributedTrackCacheAI(self.air, self, pos.getX(), pos.getY(), pos.getZ())
        cache.generateWithRequired(zoneId)
        self.cache = cache
        self.planner.sendUpdate('actionAnnounce', [ActionGlobals.ANNOUNCE_CACHE_SPAWNED, 0, 0])

    def _removeCache(self):
        if self.cache is not None:
            cache = self.cache
            self.cache = None
            cache.director = None
            if not cache.isDeleted():
                cache.requestDelete()

    def onCacheGrabbed(self, avId, toon):
        """Grant a random undiscovered track (or beans).  Returns (track, beans)."""
        self.cache = None
        undiscovered = ActionProgression.getUndiscoveredTracks(toon)
        beans = 0
        track = -1
        if undiscovered:
            track = self.rng.choice(undiscovered)
            access = list(toon.getTrackAccess() or [0] * ActionGlobals.NUM_TRACKS)
            while len(access) < ActionGlobals.NUM_TRACKS:
                access.append(0)
            access[track] = max(access[track], 1)
            toon.b_setTrackAccess(access)
        else:
            beans = int(round(ActionGlobals.CACHE_CONSOLATION_BEANS * self.profile.rewardScale))
            self._grantBeans(toon, beans)
        self._progressObjectives(cache=True)
        return track, beans

    def onCacheDeleted(self, cache):
        if self.cache is cache:
            self.cache = None

    # ------------------------------------------------------------------
    # Network helpers
    # ------------------------------------------------------------------
    def d_setTier(self):
        if self.planner is not None:
            self.planner.sendUpdate('setActionTier', [self.tier])

    def d_setPressure(self, force=False):
        if self.planner is None:
            return
        value = self.pressure.getValue()
        if not force and value == self.lastSentPressure:
            return
        self.lastSentPressure = value
        self.planner.sendUpdate('setActionPressure', [value, self.pressure.getStage()])

    def d_setObjectives(self):
        if self.planner is None:
            return
        self.objectivesDirty = False
        self.planner.sendUpdate('setActionObjectives', [[objective.toWire() for objective in self.objectives]])

    # ------------------------------------------------------------------
    # Debug / magic words
    # ------------------------------------------------------------------
    def debugSetPressure(self, value):
        if self.pressure.setValue(value):
            self._onStageChanged()
        self.d_setPressure(force=True)

    def debugSpawnCache(self):
        spot = self._pickCacheSpot()
        if spot is None or self.cache is not None:
            return False
        self._spawnCache(*spot)
        return True
