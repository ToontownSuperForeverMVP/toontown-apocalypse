import random
from direct.task import Task
from direct.distributed.ClockDelta import globalClockDelta
from toontown.toonbase import ToontownBattleGlobals

from otp.ai.AIBaseGlobal import *
from otp.avatar import DistributedAvatarAI
from . import SuitPlannerBase, SuitBase, SuitDNA
from direct.directnotify import DirectNotifyGlobal
from toontown.battle import SuitBattleGlobals
from ..battle.SuitBattleGlobals import SuitAttributes
from toontown.action import ActionGlobals, ActionProgression


class DistributedSuitBaseAI(DistributedAvatarAI.DistributedAvatarAI, SuitBase.SuitBase):
    notify = DirectNotifyGlobal.directNotify.newCategory('DistributedSuitBaseAI')

    def __init__(self, air, suitPlanner):
        DistributedAvatarAI.DistributedAvatarAI.__init__(self, air)
        SuitBase.SuitBase.__init__(self)
        self.sp = suitPlanner
        self.maxHP = 10
        self.currHP = 10
        self.zoneId = 0
        self.dna = None
        self.virtual = 0
        self.skeleRevives = 0
        self.maxSkeleRevives = 0
        self.immune = 0
        self.reviveFlag = 0
        self.buildingHeight = None
        self.effectHandler = None
        self.freeGagLastHitByToon = {}
        # Real-time combat (Toontown Apocalypse)
        self.actionController = None
        self.actionState = ActionGlobals.COG_PATROL
        self.actionDefeated = False
        return

    def generate(self):
        DistributedAvatarAI.DistributedAvatarAI.generate(self)

    def delete(self):
        taskMgr.remove(self.taskName('freeGagHitDeath'))
        if self.actionController is not None:
            self.actionController.cleanup()
            self.actionController = None
        self.sp = None
        del self.dna
        DistributedAvatarAI.DistributedAvatarAI.delete(self)
        return

    def requestRemoval(self):
        if self.sp != None:
            self.sp.removeSuit(self)
        else:
            self.requestDelete()
        return

    def setLevel(self, lvl=None):
        attributes: SuitAttributes = SuitBattleGlobals.getSuitAttributes(self.dna.name)

        # todo maybe something smarter than this
        # todo if no level given, just pick a random one that this suit is typically allowed to be.
        if lvl is None:
            return self.setLevel(lvl=attributes.tier+1 + random.randint(0, 4))

        self.level = lvl - attributes.tier - 1

        self.notify.debug(f'Assigning level {lvl}')
        if hasattr(self, 'doId'):
            self.d_setLevelDist(self.level)

        hp = attributes.getBaseMaxHp(self.getActualLevel())
        self.maxHP = hp
        self.currHP = hp

    def getLevelDist(self):
        return self.getLevel()

    def d_setLevelDist(self, level):
        self.sendUpdate('setLevelDist', [level])

    def setupSuitDNA(self, level, type, track, name=None):
        dna = SuitDNA.SuitDNA()
        if name and name in SuitDNA.notMainTypes:
            dna.newSuitName(track, name)
        else:
            dna.newSuitRandom(type, track)
        self.dna = dna
        self.track = track
        self.setLevel(level)
        return None

    def getDNAString(self):
        if self.dna:
            return self.dna.makeNetString()
        else:
            self.notify.debug('No dna has been created for suit %d!' % self.getDoId())
            return ''

    def b_setBrushOff(self, index):
        self.setBrushOff(index)
        self.d_setBrushOff(index)
        return None

    def d_setBrushOff(self, index):
        self.sendUpdate('setBrushOff', [index])

    def setBrushOff(self, index):
        pass

    def d_denyBattle(self, toonId):
        self.sendUpdateToAvatarId(toonId, 'denyBattle', [])

    def b_setImmuneStatus(self, num):
        if num == None:
            num = 0
        self.setImmuneStatus(num)
        self.d_setImmuneStatus(self.getImmuneStatus())
        return

    def d_setImmuneStatus(self, num):
        self.sendUpdate('setImmuneStatus', [num])

    def getImmuneStatus(self):
        return self.immune

    def setImmuneStatus(self, num):
        if num == None:
            num = 0
        self.immune = num
        return

    def b_setSkeleRevives(self, num):
        if num == None:
            num = 0
        self.setSkeleRevives(num)
        self.d_setSkeleRevives(self.getSkeleRevives())
        return

    def d_setSkeleRevives(self, num):
        self.sendUpdate('setSkeleRevives', [num])

    def getSkeleRevives(self):
        return self.skeleRevives

    def setSkeleRevives(self, num):
        if num == None:
            num = 0
        self.skeleRevives = num
        if num > self.maxSkeleRevives:
            self.maxSkeleRevives = num
        return

    def getMaxSkeleRevives(self):
        return self.maxSkeleRevives

    def useSkeleRevive(self):
        self.skeleRevives -= 1
        self.currHP = self.maxHP
        self.reviveFlag = 1

    def reviveCheckAndClear(self):
        returnValue = 0
        if self.reviveFlag == 1:
            returnValue = 1
            self.reviveFlag = 0
        return returnValue

    def getHP(self):
        return self.currHP

    def setHP(self, hp):
        if hp > self.maxHP:
            self.currHP = self.maxHP
        else:
            self.currHP = hp
        return None

    def b_setHP(self, hp):
        self.setHP(hp)
        self.d_setHP(hp)

    def d_setHP(self, hp):
        self.sendUpdate('setHP', [hp])

    # ------------------------------------------------------------------
    # Real-time combat (Toontown Apocalypse)
    # ------------------------------------------------------------------
    def getActionDirector(self):
        director = getattr(self.sp, 'actionDirector', None)
        if director is not None:
            return director
        controller = getattr(self, 'actionController', None)
        return getattr(controller, 'director', None)

    def b_setActionState(self, state):
        self.actionState = state
        self.sendUpdate('setActionState', [state])

    def getActionState(self):
        return self.actionState

    def d_setSmPosHpr(self, x, y, z, h, p, r):
        self.sendUpdate('setSmPosHpr', [x, y, z, h, p, r, globalClockDelta.getFrameNetworkTime()])

    def d_setSmStop(self):
        self.sendUpdate('setSmStop', [globalClockDelta.getFrameNetworkTime()])

    def isActionTargetable(self):
        """Can this Cog be hit by free-aim gags right now?"""
        if self.currHP <= 0 or self.actionDefeated or self.getImmuneStatus():
            return False
        battleTrap = getattr(self, 'battleTrap', None)
        if battleTrap is not None and battleTrap != -1:
            return False
        # Factory/mint/stage suits use a battle cell instead of pathState and
        # still run the legacy turn-based battles.
        if getattr(self, 'battleCellIndex', None) is not None:
            return False
        # Street Cogs that fly away / dance are no longer valid targets.
        pathState = getattr(self, 'pathState', None)
        if pathState in (2, 4):
            return False
        return True

    def requestFreeGagHit(self, track, level):
        """Validate and apply a first-person gag hit.

        The client chooses the Cog under its crosshair for presentation, but
        it cannot choose damage or hit a Cog from across the map.  Tier,
        equipped tracks, range, cooldowns and status bonuses are all checked
        here; rewards flow through the street director.
        """
        toonId = self.air.getAvatarIdFromSender()
        toon = self.air.doId2do.get(toonId)
        if toon is None or getattr(toon, 'hp', 0) <= 0:
            return
        if hasattr(toon, 'getBattleId') and toon.getBattleId() > 0:
            return
        if not self.isActionTargetable():
            return

        gagDef = ActionGlobals.getGagDef(track, level)
        if gagDef is None or gagDef.style in (ActionGlobals.GAG_STYLE_TRAP, ActionGlobals.GAG_STYLE_HEAL):
            return
        if not ActionProgression.canUseGag(toon, track, level):
            return

        try:
            distance = (self.getPos() - toon.getPos()).length()
        except Exception:
            return
        if distance > gagDef.range + gagDef.splash + ActionGlobals.GAG_RANGE_SLACK:
            return

        now = globalClock.getFrameTime()
        director = self.getActionDirector()
        if director is not None:
            if not director.validateGagHit(self, toon, gagDef, now):
                return
        else:
            if now - self.freeGagLastHitByToon.get(toonId, -100.0) < gagDef.cooldown * 0.8:
                return
            self.freeGagLastHitByToon[toonId] = now

        if gagDef.style == ActionGlobals.GAG_STYLE_LURE:
            if director is not None:
                director.onLure(self, toon, gagDef, now)
            return

        lured, soaked = (False, False)
        if director is not None:
            lured, soaked = director.getStatusForSuit(self, now)
        damage = ActionProgression.getGagDamageForToon(toon, gagDef, lured=lured, soaked=soaked)
        self.applyActionDamage(toon, gagDef, damage, now, validate=False)

    def applyActionDamage(self, toon, gagDef, damage, now=None, validate=True):
        """Apply gag damage from ``toon`` (may be None for expired traps)."""
        if validate and not self.isActionTargetable():
            return
        if self.currHP <= 0 or self.actionDefeated:
            return
        if now is None:
            now = globalClock.getFrameTime()
        damage = max(1, int(damage))
        remainingHP = max(0, self.currHP - damage)
        toonId = toon.doId if toon is not None else 0
        track = gagDef.track if gagDef is not None else ActionGlobals.THROW_TRACK
        level = gagDef.level if gagDef is not None else 0

        # Keep the reaction and HP bar update ordered before the authoritative
        # setHP update.  Both messages are emitted by this distributed object,
        # so clients see the impact before the Cog is removed on death.
        self.sendUpdate('freeGagHit', [toonId, track, level, damage])
        self.b_setHP(remainingHP)

        director = self.getActionDirector()
        if director is not None and toon is not None and gagDef is not None:
            director.onGagHit(self, toon, gagDef, damage, now)
        elif self.actionController is not None and toon is not None:
            self.actionController.onDamaged(toon, gagDef, damage, now)

        if remainingHP <= 0:
            self._onActionDefeated(toon, gagDef)

    def _onActionDefeated(self, toon, gagDef):
        if self.actionDefeated:
            return
        self.actionDefeated = True
        director = self.getActionDirector()
        if director is not None:
            director.onCogDefeated(self, toon, gagDef)
        else:
            self._grantFallbackRewards(toon, gagDef)
            if self.actionController is not None:
                self.actionController.onDefeated()
        taskMgr.doMethodLater(ActionGlobals.COG_DEATH_DELAY, self._finishFreeGagHit,
                              self.taskName('freeGagHitDeath'))

    def _grantFallbackRewards(self, toon, gagDef):
        """Rewards for Cogs that live outside a street director."""
        level = self.getActualLevel()
        profile = ActionGlobals.getDifficultyProfile(ActionGlobals.DEFAULT_TIER,
                                                    ActionProgression.getPowerRating(toon) if toon else ActionGlobals.MIN_POWER,
                                                    0)
        beans = ActionGlobals.getKillBeans(level, profile, bool(self.getSkelecog()))
        xp = ActionGlobals.getKillXp(level, profile, bool(self.getSkelecog()))
        track = gagDef.track if gagDef is not None else ActionGlobals.THROW_TRACK
        toonId = 0
        if toon is not None:
            toonId = toon.doId
            if hasattr(toon, 'addMoney'):
                toon.addMoney(beans)
            experience = getattr(toon, 'experience', None)
            if experience is not None and ActionProgression.getTrackTier(toon, track) > 0:
                experience.addExp(track, xp)
                toon.d_setExperience(experience.getCurrentExperience())
        self.sendUpdate('actionDefeated', [toonId, beans, track, xp, int(bool(self.getSkelecog()))])

    def _finishFreeGagHit(self, task):
        if self.currHP <= 0:
            self.requestRemoval()
        return Task.done

    def releaseControl(self):
        return None

    def getDeathEvent(self):
        return 'cogDead-%s' % self.doId

    def resume(self):
        self.notify.debug('resume, hp=%s' % self.currHP)
        if self.currHP <= 0:
            messenger.send(self.getDeathEvent())
            self.requestRemoval()
        return None

    def prepareToJoinBattle(self):
        pass

    def b_setSkelecog(self, flag):
        self.setSkelecog(flag)
        self.d_setSkelecog(flag)

    def setSkelecog(self, flag):
        SuitBase.SuitBase.setSkelecog(self, flag)

    def d_setSkelecog(self, flag):
        self.sendUpdate('setSkelecog', [flag])

    def isForeman(self):
        return 0

    def isSupervisor(self):
        return 0

    def setVirtual(self, virtual):
        pass

    def getVirtual(self):
        return 0

    def isVirtual(self):
        return self.getVirtual()
