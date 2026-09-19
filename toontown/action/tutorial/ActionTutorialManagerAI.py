"""AI-side driver for the guided real-time combat tutorial.

A single global :class:`ActionTutorialManagerAI` owns one
:class:`ActionTutorialSession` per Toon being tutored.  The session is the only
place that touches gameplay: it grants the starter kit, spawns practice Cogs
and a Gag Cache, watches the Toon's position for the movement and dodge steps,
and hands out the graduation reward.

The step rules themselves live in :mod:`.ActionTutorialGlobals`; this module
only translates observed gameplay into signals for that machine and mirrors the
result to the client.
"""

import math

from panda3d.core import Point3, Vec3
from direct.directnotify import DirectNotifyGlobal
from direct.distributed.DistributedObjectAI import DistributedObjectAI
from direct.task import Task

from otp.ai.AIBaseGlobal import *  # noqa: F401,F403  (taskMgr, globalClock, simbase)

from toontown.action import ActionGlobals, ActionProgression
from toontown.action.tutorial import ActionTutorialGlobals as G
from toontown.action.tutorial.TutorialPracticeSuitAI import TutorialPracticeSuitAI


def _forwardFromHeading(heading):
    """Unit vector for a Toontown heading (0 points along +Y)."""
    radians = math.radians(heading)
    return Vec3(-math.sin(radians), math.cos(radians), 0.0)


class ActionTutorialSession(object):
    """One Toon's run through the tutorial."""

    notify = DirectNotifyGlobal.directNotify.newCategory('ActionTutorialSession')

    def __init__(self, manager, avId, streetZone):
        self.manager = manager
        self.air = manager.air
        self.avId = avId
        self.streetZone = streetZone
        self.state = G.ActionTutorialState()
        self.startPos = None
        self.practiceSuit = None
        self.dodgeSuit = None
        self.cache = None
        self.dodgeArmedAt = None
        self.dodgeStart = None
        self.dodgeAttempts = 0
        self.finished = False
        self.taskName = 'actionTutorial-%d' % avId
        self.lastBroadcast = None
        self.ticks = 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def getToon(self):
        return self.air.doId2do.get(self.avId)

    def getStep(self):
        return self.state.getStep()

    def isFinished(self):
        return self.finished

    def _sendPlayer(self, fieldName, args):
        self.manager.sendUpdateToAvatarId(self.avId, fieldName, args)

    def _sync(self, force=False):
        payload = (self.state.getStep(), self.state.getTotal())
        if force or payload != self.lastBroadcast:
            self.lastBroadcast = payload
            self._sendPlayer('setTutorialStep', list(payload))
        self._sendPlayer('setTutorialValue', [self._hintValue()])

    def _hintValue(self):
        if self.state.getStep() == G.STEP_LOADOUT:
            return self.state.getValue(G.SIGNAL_LOADOUT, 0)
        if self.state.getStep() == G.STEP_FIRE:
            return G.PRACTICE_COG_KILLS
        if self.state.getStep() == G.STEP_DODGE:
            return self.dodgeAttempts
        return G.NO_VALUE

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self):
        toon = self.getToon()
        if toon is None:
            return False
        self._grantStarterKit(toon)
        try:
            self.startPos = Point3(toon.getPos())
        except Exception:
            self.startPos = Point3(0, 0, 0)
        self._enterStep(self.state.getStep())
        self._sync(force=True)
        taskMgr.add(self._tick, self.taskName)
        return True

    def stop(self):
        taskMgr.remove(self.taskName)
        self._clearPracticeSuit()
        self._clearDodgeSuit()
        self._removeCache()

    def _grantStarterKit(self, toon):
        """Mirror StreetDirectorAI._ensureStarterKit for the tutorial street."""
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
        if toon.getMoney() < G.STARTER_BEANS and hasattr(toon, 'addMoney'):
            toon.addMoney(G.STARTER_BEANS - toon.getMoney())

    # ------------------------------------------------------------------
    # Signals / step transitions
    # ------------------------------------------------------------------
    def onPracticeCogDefeated(self, suit):
        if suit is self.practiceSuit:
            self.practiceSuit = None
        if suit is self.dodgeSuit:
            self.dodgeSuit = None
        self.signal(G.SIGNAL_KILL, 1)

    def signal(self, name, value=0):
        if self.finished:
            return False
        if not self.state.report(name, value):
            self._sync()
            return False
        self._onAdvance()
        return True

    def _onAdvance(self):
        step = self.state.getStep()
        self._clearPracticeSuit()
        self._clearDodgeSuit()
        if step > G.STEP_CACHE:
            self._removeCache()
        if self.state.isFinished():
            self._finish(True)
            return
        self._enterStep(step)
        self._sync(force=True)
        self._sendPlayer('tutorialStepComplete', [self.state.getStep() - 1, G.STEP_BEANS])

    def _enterStep(self, step):
        if step == G.STEP_MOVE:
            toon = self.getToon()
            if toon is not None:
                try:
                    self.startPos = Point3(toon.getPos())
                except Exception:
                    pass
        elif step == G.STEP_FIRE:
            self._spawnPracticeSuit()
        elif step == G.STEP_DODGE:
            self._armDodge()
        elif step == G.STEP_CACHE:
            self._spawnCache()
        elif step == G.STEP_FINISH:
            self._awardGraduation()

    # -- practice Cog ----------------------------------------------------
    def _spawnPracticeSuit(self):
        self._clearPracticeSuit()
        toon = self.getToon()
        if toon is None:
            return None
        try:
            pos = Point3(toon.getPos()) + _forwardFromHeading(toon.getH()) * 14.0
        except Exception:
            pos = Point3(toon.getPos()) + Point3(0, 14, 0)
        suit = TutorialPracticeSuitAI(self.air, session=self)
        suit.generateWithRequired(self.streetZone)
        suit.setPos(pos)
        try:
            suit.setH(toon.getH() + 180.0)
        except Exception:
            pass
        suit.currHP = suit.maxHP
        self.practiceSuit = suit
        return suit

    def _clearPracticeSuit(self):
        suit = self.practiceSuit
        self.practiceSuit = None
        if suit is not None:
            suit.tutorialSession = None
            if not suit.isDeleted():
                suit.requestDelete()

    # -- dodge -----------------------------------------------------------
    def _armDodge(self):
        self._clearDodgeSuit()
        toon = self.getToon()
        if toon is None:
            return
        self.dodgeStart = Point3(toon.getPos())
        self.dodgeArmedAt = globalClock.getFrameTime()
        try:
            pos = Point3(toon.getPos()) + _forwardFromHeading(toon.getH()) * 16.0
        except Exception:
            pos = Point3(toon.getPos()) + Point3(0, 16, 0)
        suit = TutorialPracticeSuitAI(self.air, session=self)
        suit.generateWithRequired(self.streetZone)
        suit.setPos(pos)
        try:
            suit.setH(toon.getH() + 180.0)
        except Exception:
            pass
        self.dodgeSuit = suit

    def _clearDodgeSuit(self):
        suit = self.dodgeSuit
        self.dodgeSuit = None
        self.dodgeArmedAt = None
        if suit is not None:
            suit.tutorialSession = None
            if not suit.isDeleted():
                suit.requestDelete()

    def _resolveDodge(self):
        toon = self.getToon()
        moved = 0.0
        if toon is not None and self.dodgeStart is not None:
            try:
                moved = (Point3(toon.getPos()) - self.dodgeStart).length()
            except Exception:
                moved = 0.0
        if moved >= G.DODGE_MOVE_DISTANCE:
            self.signal(G.SIGNAL_DODGE, 1)
            return
        self.dodgeAttempts += 1
        if toon is not None:
            toon.takeDamage(G.DODGE_DAMAGE)
        if self.dodgeAttempts >= G.DODGE_ATTEMPTS:
            # Never let a struggling player get stuck.
            self.signal(G.SIGNAL_DODGE, 1)
            return
        self._armDodge()
        self._sync(force=True)

    # -- Gag Cache -------------------------------------------------------
    def _spawnCache(self):
        self._removeCache()
        toon = self.getToon()
        if toon is None:
            return None
        try:
            pos = Point3(toon.getPos()) + _forwardFromHeading(toon.getH()) * 18.0
        except Exception:
            pos = Point3(toon.getPos()) + Point3(0, 18, 0)
        from toontown.action.DistributedTrackCacheAI import DistributedTrackCacheAI
        cache = DistributedTrackCacheAI(self.air, self, pos.getX(), pos.getY(), pos.getZ())
        cache.generateWithRequired(self.streetZone)
        self.cache = cache
        return cache

    def _removeCache(self):
        cache = self.cache
        self.cache = None
        if cache is not None:
            cache.director = None
            if not cache.isDeleted():
                cache.requestDelete()

    # Interface used by DistributedTrackCacheAI.
    def onCacheGrabbed(self, avId, toon):
        self.cache = None
        track = -1
        undiscovered = ActionProgression.getUndiscoveredTracks(toon)
        if undiscovered:
            track = undiscovered[0]
            access = list(toon.getTrackAccess() or [0] * ActionGlobals.NUM_TRACKS)
            while len(access) < ActionGlobals.NUM_TRACKS:
                access.append(0)
            access[track] = max(access[track], 1)
            toon.b_setTrackAccess(access)
        self.signal(G.SIGNAL_CACHE, 1)
        return track, 0

    def onCacheDeleted(self, cache):
        if self.cache is cache:
            self.cache = None

    # -- graduation ------------------------------------------------------
    def _awardGraduation(self):
        toon = self.getToon()
        if toon is None:
            return
        if hasattr(toon, 'addMoney'):
            toon.addMoney(G.FINISH_BEANS)
        track = None
        equipped = ActionProgression.getEquippedTracks(toon)
        if equipped:
            track = equipped[0]
        experience = getattr(toon, 'experience', None)
        if track is not None and experience is not None and ActionProgression.getTrackTier(toon, track) > 0:
            experience.addExp(track, G.FINISH_MASTERY)
            toon.d_setExperience(experience.getCurrentExperience())

    def _finish(self, success):
        if self.finished:
            return
        self.finished = True
        self.stop()
        toon = self.getToon()
        if toon is not None and hasattr(toon, 'b_setTutorialAck'):
            toon.b_setTutorialAck(1)
        self._sendPlayer('tutorialFinished', [1 if success else 0])

    # ------------------------------------------------------------------
    # Tick: watches position for MOVE / DODGE and loadout for LOADOUT.
    # ------------------------------------------------------------------
    def _tick(self, task):
        if self.finished:
            return Task.done
        toon = self.getToon()
        if toon is None:
            self._finish(False)
            return Task.done
        self.ticks += 1
        # Re-send the step occasionally so a client that generated the manager
        # a little late still ends up with the coach on screen.
        if self.ticks % 5 == 0:
            self._sync(force=True)
        step = self.state.getStep()
        if step == G.STEP_MOVE:
            self._tickMove(toon)
        elif step == G.STEP_LOADOUT:
            self._tickLoadout(toon)
        elif step == G.STEP_DODGE:
            self._tickDodge(toon)
        return Task.cont

    def _tickMove(self, toon):
        if self.startPos is None:
            return
        try:
            distance = (Point3(toon.getPos()) - self.startPos).length()
        except Exception:
            return
        if distance >= G.MOVE_START_FORWARD:
            self.signal(G.SIGNAL_MOVE, 1)

    def _tickLoadout(self, toon):
        equipped = ActionProgression.getEquippedTracks(toon)
        if len(equipped) >= G.LOADOUT_TARGET:
            self.signal(G.SIGNAL_LOADOUT, len(equipped))

    def _tickDodge(self, toon):
        if self.dodgeArmedAt is None:
            return
        if globalClock.getFrameTime() - self.dodgeArmedAt < G.DODGE_TELEGRAPH_TIME:
            return
        self._resolveDodge()


class ActionTutorialManagerAI(DistributedObjectAI):
    """Global manager: one session per Toon being walked through the tutorial."""

    notify = DirectNotifyGlobal.directNotify.newCategory('ActionTutorialManagerAI')

    def __init__(self, air):
        DistributedObjectAI.__init__(self, air)
        self.sessions = {}

    # ------------------------------------------------------------------
    # Session control (called by TutorialManagerAI)
    # ------------------------------------------------------------------
    def startSession(self, avId, streetZone):
        session = self.sessions.get(avId)
        if session is not None:
            return session
        session = ActionTutorialSession(self, avId, streetZone)
        if not session.start():
            self.notify.warning('Could not start an action tutorial for %s' % avId)
            return None
        self.sessions[avId] = session
        toon = self.air.doId2do.get(avId)
        if toon is not None:
            self.acceptOnce(self.air.getAvatarExitEvent(avId), self.endSession, extraArgs=[avId])
        return session

    def endSession(self, avId):
        session = self.sessions.pop(avId, None)
        if session is not None:
            session.stop()

    def getSession(self, avId):
        return self.sessions.get(avId)

    # ------------------------------------------------------------------
    # Network
    # ------------------------------------------------------------------
    def requestAdvance(self):
        avId = self.air.getAvatarIdFromSender()
        session = self.sessions.get(avId)
        if session is None:
            return
        if session.state.requiresAdvance():
            session.signal(G.SIGNAL_ADVANCE, 1)
        else:
            session._sync(force=True)

    def requestSkip(self):
        avId = self.air.getAvatarIdFromSender()
        session = self.sessions.get(avId)
        if session is not None:
            session._finish(False)
