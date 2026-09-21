"""Exhaustive headless test-suite for the real-time street rogue-lite.

Run from the repository root with the bundled interpreter::

    ./Panda3D/python/ppython.exe tools/action_fulltest.py

Where ``tools/action_selftest.py`` covers the pure balance modules, this suite
drives *every* feature of the street loop end to end without booting a client
or an AI:

* every tunable in :mod:`ActionGlobals` (tiers, pressure, gags, rewards, roles)
* the rolling Cog Pressure meter in every transition
* :class:`CogAttackRegistry` for every legacy attack, every mutation level and
  every registered Cog
* the :class:`CogCombatControllerAI` state machine (patrol/alert/engage/
  stagger/lured/defeated/departing, hit resolution per attack shape, knockback,
  statuses and position streaming)
* the :class:`StreetDirectorAI` (run lifecycle, spawning, rewards, objectives,
  traps, toon-up, gag caches, engage slots, pressure sync, death handling)
* the AI-side hit validation on :class:`DistributedSuitBaseAI`
* the client HUD objective formatter
* the dc network contract

Panda globals (``globalClock``) are replaced with a controllable fake clock so
the 10 Hz director ticks are fully deterministic.
"""

import builtins
import os
import random
import re
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from panda3d.core import Filename, Point3, Vec3, Vec4, loadPrcFileData  # noqa: E402

loadPrcFileData('action-fulltest', 'language english')

from panda3d.toontown import SuitLeg  # noqa: E402

from toontown.action import ActionGlobals, ActionProgression  # noqa: E402
from toontown.action.cog import CogAttackRegistry  # noqa: E402
from toontown.action.cog.CogCombatControllerAI import CogCombatControllerAI  # noqa: E402
from toontown.action.director.BuildingActionDirectorAI import BuildingActionDirectorAI  # noqa: E402
from toontown.action.director.PressureDirector import PressureMeter  # noqa: E402
from toontown.action.director.StreetDirectorAI import StreetDirectorAI  # noqa: E402
from toontown.action.objectives import ObjectiveGenerator  # noqa: E402
from toontown.action.objectives.ObjectiveGenerator import Objective  # noqa: E402
from toontown.action.tutorial import ActionTutorialGlobals as TutorialGlobals  # noqa: E402
from toontown.action.ui.ActionHUD import describeObjective  # noqa: E402
from toontown.toonbase import TTLocalizer, ToontownGlobals  # noqa: E402
from toontown.battle import SuitBattleGlobals  # noqa: E402
from toontown.battle.SuitBattleGlobals import SuitAttackType  # noqa: E402

# The AI modules resolve ``globalClock`` at call time from the builtins that
# ``otp.ai.AIBaseGlobal`` installs.  Outside a running AI that global is absent,
# so give every module we exercise a deterministic fake.
import toontown.action.director.StreetDirectorAI as _directorModule  # noqa: E402

# ``otp.ai.AIBase`` reads the ``game`` builtin that the real AI launcher sets
# up.  Provide a stand-in so the AI-side module can be imported headless.
if not hasattr(builtins, 'game'):
    builtins.game = types.SimpleNamespace(name='toontown')
import toontown.suit.DistributedSuitBaseAI as _suitModule  # noqa: E402


class FakeClock(object):
    def __init__(self):
        self.time = 0.0

    def getFrameTime(self):
        return self.time

    def advance(self, dt):
        self.time += dt

    def set(self, time):
        self.time = time


CLOCK = FakeClock()
_directorModule.globalClock = CLOCK
_suitModule.globalClock = CLOCK

failures = []
_checks = [0]


def check(condition, message):
    _checks[0] += 1
    if not condition:
        failures.append(message)
        print('FAIL: %s' % message)


def eq(actual, expected, message):
    check(actual == expected, '%s (got %r, expected %r)' % (message, actual, expected))


def approx(actual, expected, message, tolerance=1e-6):
    check(abs(actual - expected) <= tolerance, '%s (got %r, expected %r)' % (message, actual, expected))


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------
class FakeExperience(object):
    def __init__(self, values=None):
        self.experience = list(values) if values else [0] * ActionGlobals.NUM_TRACKS

    def getExp(self, track):
        return self.experience[track]

    def zeroOutExp(self):
        self.experience = [0] * ActionGlobals.NUM_TRACKS
        return self.experience

    def addExp(self, track, amount):
        self.experience[track] += amount
        return self.experience[track]

    def getCurrentExperience(self):
        return list(self.experience)


class FakeToon(object):
    _nextId = [1]

    def __init__(self, access=None, xp=None, equipped=None, maxHp=15, money=0, hp=None,
                 pos=(0, 0, 0), zoneId=1100):
        self.doId = FakeToon._nextId[0]
        FakeToon._nextId[0] += 1
        self.trackArray = list(access) if access is not None else [0] * ActionGlobals.NUM_TRACKS
        self.experience = FakeExperience(xp)
        self.equippedTracks = list(equipped or [])
        self.maxHp = maxHp
        self.hp = maxHp if hp is None else hp
        self.money = money
        self.maxMoney = 40
        self.zoneId = zoneId
        self.pos = Point3(pos)
        self.deathEvent = 'toonSad-%d' % self.doId
        self.damageMultiplier = 100
        self.expUpdates = []
        self.damageTaken = 0
        self.tutorialAck = 0
        self.equipRequests = []
        self.tierPurchases = []
        self.quests = ['quest']
        self.questHistory = ['history']
        self.rewardHistory = (1, ['reward'])
        self.inventoryUpdates = []
        self.inventory = types.SimpleNamespace(maxInventory=lambda: None,
                                              makeNetString=lambda: 'inv-net-string')

    def getDoId(self):
        return self.doId

    def uniqueName(self, name):
        return 'toon-%d-%s' % (self.doId, name)

    def b_setQuests(self, quests):
        self.quests = list(quests)

    def b_setQuestHistory(self, history):
        self.questHistory = list(history)

    def b_setRewardHistory(self, index, history):
        self.rewardHistory = (index, list(history))

    def b_setHp(self, hp):
        self.hp = hp

    def b_setMaxHp(self, hp):
        self.maxHp = hp

    def d_setInventory(self, netString):
        self.inventoryUpdates.append(netString)

    def d_requestEquipTracks(self, tracks):
        self.equipRequests.append(list(tracks))

    def d_requestBuyGagTier(self, track):
        self.tierPurchases.append(track)

    # DistributedToon accessors the action code relies on.
    def getTrackAccess(self):
        return list(self.trackArray)

    def b_setTrackAccess(self, access):
        self.trackArray = list(access)

    def getEquippedTracks(self):
        return [track for track in self.equippedTracks if track >= 0]

    def b_setEquippedTracks(self, tracks):
        self.equippedTracks = list(tracks)

    def getMaxHp(self):
        return self.maxHp

    def getMoney(self):
        return self.money

    def getMaxMoney(self):
        return self.maxMoney

    def b_setMaxMoney(self, amount):
        self.maxMoney = amount

    def addMoney(self, amount):
        self.money = min(self.maxMoney, self.money + amount)
        return self.money

    def getPos(self):
        return Point3(self.pos)

    def setPos(self, pos):
        self.pos = Point3(pos)

    def getGoneSadMessage(self):
        return self.deathEvent

    def getHp(self):
        return self.hp

    def toonUp(self, amount):
        self.hp = min(self.maxHp, self.hp + amount)
        return self.hp

    def takeDamage(self, amount):
        self.hp = max(0, self.hp - amount)
        self.damageTaken += amount
        return self.hp

    def d_setExperience(self, experience):
        self.expUpdates.append(list(experience))

    def getDamageMultiplier(self):
        return self.damageMultiplier

    def getTutorialAck(self):
        return self.tutorialAck

    def b_setTutorialAck(self, value):
        self.tutorialAck = value


class FakeDNA(object):
    def __init__(self, name):
        self.name = name


class FakeSuit(object):
    _nextId = [1]

    def __init__(self, planner=None, level=1, name='f', skelecog=0, pos=(0, 0, 0), legType=None):
        self.doId = FakeSuit._nextId[0]
        FakeSuit._nextId[0] += 1
        self.sp = planner
        self.dna = FakeDNA(name)
        self.level = level
        attributes = SuitBattleGlobals.getSuitAttributes(name)
        self.maxHP = attributes.getBaseMaxHp(level)
        self.currHP = self.maxHP
        self.skele = skelecog
        self.pathPos = Point3(pos)
        self.pos = Point3(pos)
        self.heading = 0.0
        self.legType = SuitLeg.TWalk if legType is None else legType
        self.actionState = None
        self.actionControlled = False
        self.actionElite = bool(skelecog)
        self.updates = []
        self.flewAway = False
        self.actionDamage = []
        self.removed = False

    def getActualLevel(self):
        return self.level

    def getSkelecog(self):
        return self.skele

    def getCurrentPathPos(self):
        return Point3(self.pathPos)

    def beginActionControl(self):
        self.actionControlled = True

    def setPos(self, pos):
        self.pos = Point3(pos)

    def setH(self, heading):
        self.heading = heading

    def getPos(self):
        return Point3(self.pos)

    def getHP(self):
        return self.currHP

    def b_setActionState(self, state):
        self.actionState = state

    def sendUpdate(self, name, args):
        self.updates.append((name, list(args)))

    def d_setSmPosHpr(self, x, y, z, h, p, r):
        self.updates.append(('smPosHpr', [x, y, z, h, p, r]))

    def d_setSmStop(self):
        self.updates.append(('smStop', []))

    def flyAwayNow(self):
        self.flewAway = True

    def applyActionDamage(self, toon, gagDef, damage, now=None, validate=True):
        self.actionDamage.append((toon, gagDef, damage))
        self.currHP = max(0, self.currHP - damage)
        return self.currHP

    def requestRemoval(self):
        self.removed = True


class FakePoint(object):
    def __init__(self, pos):
        self._pos = Point3(pos)

    def getPos(self):
        return Point3(self._pos)


class FakePlanner(object):
    SUIT_HOOD_INFO_ZONE = 0
    SUIT_HOOD_INFO_MIN = 1
    SUIT_HOOD_INFO_MAX = 2
    SUIT_HOOD_INFO_TRACK = 8
    SUIT_HOOD_INFO_LVL = 9
    TOTAL_MAX_SUITS = 30

    def __init__(self, air, zoneId=1100, hoodInfoIdx=None):
        self.air = air
        self.zoneId = zoneId
        self.doId = 9001
        # None means "no hood info": the director must fall back to defaults.
        self.hoodInfoIdx = 0 if hoodInfoIdx is None else hoodInfoIdx
        # [zone, min, max, ..., track weights, levels]
        self.SuitHoodInfo = [[zoneId, 3, 8, 0, 0, 0, 4, 0, (25, 25, 25, 25), (1, 2, 3), []]]
        self.suitList = []
        self.streetPointList = []
        self.pointIndexes = {}
        self.pointMap = {}
        self.updates = []
        self.avatarUpdates = []
        self.createdSuits = []
        self.actionDirector = None

    def sendUpdate(self, name, args, *extra):
        self.updates.append((name, list(args)))

    def sendUpdateToAvatarId(self, avId, name, args):
        self.avatarUpdates.append((avId, name, list(args)))

    def createNewSuit(self, *args, **kwargs):
        suit = FakeSuit(planner=self, level=kwargs.get('suitLevel', 1),
                        skelecog=kwargs.get('skelecog') or 0, name='f')
        self.suitList.append(suit)
        self.createdSuits.append(suit)
        if self.actionDirector is not None:
            self.actionDirector.registerSuit(suit)
        return suit

    def getZoneIdToPointMap(self):
        return self.pointMap

    def genPath(self, start, end, zone, maxPoints):
        return None

    def getCurrentPathPos(self):
        return Point3(0, 0, 0)

    def actionUpdates(self, name):
        return [args for (sent, args) in self.updates if sent == name]


class FakeAir(object):
    def __init__(self):
        self.doId2do = {}


class FakeDirector(object):
    """Minimal director double used to unit-test the Cog controller."""

    def __init__(self, toons=None, profile=None, runActive=True, engageLimit=99):
        self.toons = list(toons or [])
        self.profile = profile or ActionGlobals.getDifficultyProfile(1, ActionGlobals.MIN_POWER, 0)
        self.runActive = runActive
        self.engageLimit = engageLimit
        self.slots = {}
        self.attacks = []
        self.resolutions = []
        self.controllerList = []

    def isRunActive(self):
        return self.runActive and bool(self.toons)

    def getEngageableToons(self):
        return [toon for toon in self.toons if getattr(toon, 'hp', 0) > 0]

    def getActiveToon(self, avId):
        for toon in self.toons:
            if toon.doId == avId and toon.hp > 0:
                return toon
        return None

    def getToonVelocity(self, avId):
        return Vec3(0, 0, 0)

    def requestEngageSlot(self, avId, controller, force=False):
        slots = self.slots.setdefault(avId, set())
        if controller in slots:
            return True
        if not force and len(slots) >= self.engageLimit:
            return False
        slots.add(controller)
        return True

    def releaseEngageSlot(self, controller):
        for slots in self.slots.values():
            slots.discard(controller)

    def canAttackToon(self, avId, now):
        return True

    def noteAttackStart(self, avId, now):
        self.attacks.append((avId, now))

    def onAttackResolved(self, controller, toon, realtime, damage):
        self.resolutions.append((controller, toon, realtime, damage))

    def getEngagedControllers(self):
        return [controller for controller in self.controllerList if controller.isEngaged()]


class StubRandom(random.Random):
    """Deterministic RNG: every roll is 0 and every pick is the first option."""

    def random(self):
        return 0.0

    def uniform(self, a, b):
        return a

    def randrange(self, *args):
        return 0

    def choice(self, seq):
        seq = list(seq)
        return seq[0] if seq else None

    def shuffle(self, seq):
        return None


class FakeSuitController(object):
    """Controller-ish double for director tests that do not need the brain."""

    def __init__(self, suit):
        self.suit = suit
        self.pos = Point3(0, 0, 0)
        self.havePos = True
        self.targetId = 0
        self.defeated = False
        self.lured = False
        self.soaked = False
        self.cleaned = False
        self.damage = None

    def isActive(self):
        return True

    def isEngaged(self):
        return True

    def isLured(self, now):
        return self.lured

    def isSoaked(self, now):
        return self.soaked

    def getTargetId(self):
        return self.targetId

    def onDamaged(self, toon, gagDef, damage, now):
        self.damage = (toon, gagDef, damage)

    def onDefeated(self):
        self.defeated = True

    def applyLure(self, toon, gagDef, now):
        self.lured = True

    def cleanup(self):
        self.cleaned = True

    def tick(self, now, dt, profile):
        pass


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
def testTunables():
    eq(len(ActionGlobals.TRACK_NAMES), ActionGlobals.NUM_TRACKS, 'one name per track')
    eq(len(ActionGlobals.TRACK_SHORT_NAMES), ActionGlobals.NUM_TRACKS, 'one short name per track')
    eq(len(ActionGlobals.TRACK_COLORS), ActionGlobals.NUM_TRACKS, 'one color per track')
    eq(len(ActionGlobals.PRESSURE_STAGE_NAMES), ActionGlobals.NUM_PRESSURE_STAGES, 'stage names')
    eq(len(ActionGlobals.PRESSURE_STAGE_THRESHOLDS), ActionGlobals.NUM_PRESSURE_STAGES, 'stage thresholds')
    eq(len(ActionGlobals.PRESSURE_STAGE_COLORS), ActionGlobals.NUM_PRESSURE_STAGES, 'stage colors')
    eq(len(ActionGlobals.PRESSURE_STAGE_REWARD_MULT), ActionGlobals.NUM_PRESSURE_STAGES, 'stage rewards')
    eq(len(ActionGlobals.PRESSURE_STAGE_LEVEL_OFFSET), ActionGlobals.NUM_PRESSURE_STAGES, 'stage level offsets')
    thresholds = ActionGlobals.PRESSURE_STAGE_THRESHOLDS
    eq(thresholds[0], 0, 'first pressure threshold is 0')
    check(all(thresholds[i] < thresholds[i + 1] for i in range(len(thresholds) - 1)),
          'pressure thresholds strictly increase')
    check(thresholds[-1] < ActionGlobals.MAX_PRESSURE, 'top threshold inside the meter range')
    rewards = ActionGlobals.PRESSURE_STAGE_REWARD_MULT
    check(all(rewards[i] < rewards[i + 1] for i in range(len(rewards) - 1)),
          'pressure reward multipliers strictly increase')
    eq(len(ActionGlobals.PRESSURE_STAGE_COLORS[0]), 4, 'stage colors are rgba')
    eq(len(ActionGlobals.ROLE_TUNING), len(ActionGlobals.ROLE_NAMES), 'role tuning per role name')
    check(len(ActionGlobals.ROLE_BY_TIER) >= 8, 'role table covers tiers 0-7')
    for tier in range(8):
        check(ActionGlobals.ROLE_BY_TIER[tier] in ActionGlobals.ROLE_TUNING, 'role for tier %d' % tier)
    for name, role in ActionGlobals.ROLE_OVERRIDES.items():
        check(role in ActionGlobals.ROLE_TUNING, 'override role for %s' % name)
    eq(ActionGlobals.TRACK_TIER_XP[0], 0, 'tier I has no XP gate')
    eq(ActionGlobals.TRACK_TIER_COST[0], 0, 'tier I has no bean cost')
    eq(len(ActionGlobals.TRACK_TIER_XP), ActionGlobals.MAX_TRACK_TIER, 'one xp gate per tier')
    eq(len(ActionGlobals.TRACK_TIER_COST), ActionGlobals.MAX_TRACK_TIER, 'one cost per tier')
    eq(len(ActionGlobals.TRACK_TIER_POWER), ActionGlobals.MAX_TRACK_TIER + 1, 'power per owned tier')
    for track in ActionGlobals.STARTER_TRACKS:
        check(0 <= track < ActionGlobals.NUM_TRACKS, 'starter track in range')
    check(ActionGlobals.MAX_JELLYBEANS > 40, 'rogue-lite wallet exceeds the classic cap')
    eq(len(ActionGlobals.DEPT_CODES), 4, 'four departments')
    eq(len(ActionGlobals.DEPT_NAMES), 4, 'four department names')
    for code in ActionGlobals.DEPT_CODES:
        eq(len(code), 1, 'single letter department code')


# ---------------------------------------------------------------------------
# Pressure
# ---------------------------------------------------------------------------
def testPressure():
    for value, expected in ((0, 0), (99, 0), (100, 1), (219, 1), (220, 2), (359, 2),
                            (360, 3), (519, 3), (520, 4), (699, 4), (700, 5), (1000, 5)):
        eq(ActionGlobals.getPressureStage(value), expected, 'stage(%d)' % value)
    stage, fraction = ActionGlobals.getPressureStageFraction(0)
    eq(stage, 0, 'fraction stage at 0')
    approx(fraction, 0.0, 'fraction at 0')
    stage, fraction = ActionGlobals.getPressureStageFraction(50)
    eq(stage, 0, 'fraction stage at 50')
    approx(fraction, 0.5, 'fraction mid-stage')
    stage, fraction = ActionGlobals.getPressureStageFraction(700)
    eq(stage, 5, 'fraction stage at 700')
    approx(fraction, 0.0, 'fraction at last threshold')
    stage, fraction = ActionGlobals.getPressureStageFraction(1000)
    eq(stage, 5, 'fraction stage at max')
    approx(fraction, 1.0, 'fraction at max')

    meter = PressureMeter(tier=5)
    eq(meter.getStageName(), 'CALM', 'fresh meter is CALM')
    eq(meter.getValue(), 0, 'fresh meter value')
    check(not meter.tick(0.0, True), 'zero dt tick does nothing')
    check(not meter.tick(-1.0, True), 'negative dt tick does nothing')
    meter.tick(10.0, True)
    check(meter.getValue() > 0, 'pressure rises with toons present')
    changed = False
    for _ in range(600):
        changed = meter.tick(1.0, True) or changed
    check(changed and meter.getStage() >= ActionGlobals.PRESSURE_INVASION, 'ten minutes at tier 5 hits INVASION')
    check(meter.peakValue >= meter.getValue(), 'peak tracks the maximum')
    meter.onToonDied()
    check(meter.getValue() <= ActionGlobals.MAX_PRESSURE * ActionGlobals.PRESSURE_DEATH_MULTIPLIER + 1,
          'death halves pressure')
    for _ in range(120):
        meter.tick(1.0, False)
    eq(meter.getStage(), ActionGlobals.PRESSURE_CALM, 'pressure decays to CALM')
    eq(meter.getValue(), 0, 'pressure decays to zero')

    meter.setTier(99)
    eq(meter.tier, ActionGlobals.MAX_TIER, 'tier clamps high')
    meter.setTier(-5)
    eq(meter.tier, ActionGlobals.MIN_TIER, 'tier clamps low')

    meter.reset()
    eq(meter.getValue(), 0, 'reset clears value')
    eq(meter.peakValue, 0.0, 'reset clears peak')
    eq(meter.getStage(), ActionGlobals.PRESSURE_CALM, 'reset clears stage')

    meter.reset()
    meter.setValue(ActionGlobals.PRESSURE_STAGE_THRESHOLDS[1] - 5)
    check(meter.onCogDefeated(10, elite=True), 'elite kill can cross a stage')
    meter.reset()
    before = meter.getValue()
    meter.onDodge()
    check(meter.getValue() > before, 'dodge adds pressure')
    meter.addRaw(-9999)
    eq(meter.getValue(), 0, 'addRaw clamps at zero')
    meter.setValue(1500)
    eq(meter.getValue(), ActionGlobals.MAX_PRESSURE, 'setValue clamps at max')
    eq(meter.getStage(), ActionGlobals.PRESSURE_INVASION, 'setValue updates stage')
    meter.setValue(-500)
    eq(meter.getValue(), 0, 'setValue clamps at zero')
    meter.reset()
    meter.onObjectiveComplete()
    eq(meter.getValue(), int(ActionGlobals.PRESSURE_PER_OBJECTIVE), 'objective adds pressure')


# ---------------------------------------------------------------------------
# Difficulty profile
# ---------------------------------------------------------------------------
def testProfiles():
    weak = ActionGlobals.getPowerRating([0, 0, 0, 0, 1, 1, 0], [0] * 7, [4, 5], 15)
    strong = ActionGlobals.getPowerRating([3] * 7, [ActionGlobals.MAX_TRACK_XP] * 7, [4, 5, 6], 137)
    approx(weak, ActionGlobals.MIN_POWER, 'starter kit power is MIN_POWER', tolerance=1.0)
    approx(strong, ActionGlobals.MAX_POWER, 'maxed kit power is MAX_POWER', tolerance=1.0)
    check(ActionGlobals.getRelativeStrength(weak) < 0.05, 'weak relative strength is near zero')
    check(ActionGlobals.getRelativeStrength(strong) > 0.95, 'strong relative strength is near one')
    approx(ActionGlobals.getRelativeStrength(ActionGlobals.MIN_POWER - 100), 0.0, 'relative strength floors')
    approx(ActionGlobals.getRelativeStrength(ActionGlobals.MAX_POWER + 100), 1.0, 'relative strength caps')

    lastOffset = -1
    for tier in range(ActionGlobals.MIN_TIER, ActionGlobals.MAX_TIER + 1):
        profile = ActionGlobals.getDifficultyProfile(tier, weak, 0)
        check(profile.levelOffset >= lastOffset, 'level offset never drops with tier')
        lastOffset = profile.levelOffset
        check(profile.spawnInterval >= 3.0, 'spawn interval floor at tier %d' % tier)
        check(0.0 < profile.eliteChance <= 0.6, 'elite chance in range at tier %d' % tier)
        check(profile.engageLimit >= 2, 'engage limit at least two')
        check(profile.rewardScale >= 1.0, 'reward scale at least one')
        eq(profile.xpScale, profile.rewardScale, 'xp scale mirrors reward scale')
    weakTen = ActionGlobals.getDifficultyProfile(10, weak, 0)
    strongTen = ActionGlobals.getDifficultyProfile(10, strong, 0)
    check(strongTen.levelOffset > weakTen.levelOffset, 'tier 10 is harder for a strong kit')
    check(weakTen.levelOffset >= 2, 'tier 10 still bites a weak kit')
    invasion = ActionGlobals.getDifficultyProfile(10, strong, ActionGlobals.PRESSURE_STAGE_THRESHOLDS[5])
    check(invasion.rewardScale > strongTen.rewardScale, 'pressure raises rewards')
    eq(invasion.attackMutation, CogAttackRegistry.MAX_MUTATION, 'tier 10 invasion maxes mutations')
    check(invasion.engageLimit > strongTen.engageLimit, 'invasion allows more attackers')
    check(invasion.detectRange > strongTen.detectRange, 'invasion raises detection range')

    eq(ActionGlobals.getDifficultyProfile(99, weak, 0).tier, ActionGlobals.MAX_TIER, 'tier clamps high')
    eq(ActionGlobals.getDifficultyProfile(0, weak, 0).tier, ActionGlobals.MIN_TIER, 'tier clamps low')

    preview = ActionGlobals.previewTier(5, weak)
    eq(preview.tier, 5, 'preview tier')
    eq(preview.stage, ActionGlobals.PRESSURE_CALM, 'preview assumes calm')

    rng = random.Random(3)
    for level in range(1, 13):
        for offset in (-5, 0, 5):
            profile = ActionGlobals.DifficultyProfile(levelOffset=offset)
            picked = ActionGlobals.pickSpawnLevel(profile, (level,), rng)
            check(ActionGlobals.MIN_COG_LEVEL <= picked <= ActionGlobals.MAX_COG_LEVEL,
                  'spawn level clamped (%d)' % picked)
    profile = ActionGlobals.DifficultyProfile(levelOffset=20)
    eq(ActionGlobals.pickSpawnLevel(profile, (12,), random.Random(1)), ActionGlobals.MAX_COG_LEVEL,
       'spawn level clamps at the top')
    profile = ActionGlobals.DifficultyProfile(levelOffset=-50)
    eq(ActionGlobals.pickSpawnLevel(profile, (5,), random.Random(1)), ActionGlobals.MIN_COG_LEVEL,
       'spawn level clamps at the bottom')
    eq(ActionGlobals.pickSpawnLevel(ActionGlobals.DifficultyProfile(), (), random.Random(1)),
       ActionGlobals.MIN_COG_LEVEL, 'empty level list falls back to level 1')


# ---------------------------------------------------------------------------
# Gags
# ---------------------------------------------------------------------------
def testGags():
    for track in ActionGlobals.ALL_TRACKS:
        definitions = ActionGlobals.GAG_DEFS[track]
        eq(len(definitions), ActionGlobals.MAX_TRACK_TIER, 'track %d has three tiers' % track)
        for tier, gagDef in enumerate(definitions, start=1):
            eq(gagDef.track, track, 'gag track tag %s' % gagDef.name)
            eq(gagDef.tier, tier, 'gag tier tag %s' % gagDef.name)
            eq(gagDef.level, tier - 1, 'gag level %s' % gagDef.name)
            check(gagDef.cooldown > 0, 'cooldown for %s' % gagDef.name)
            check(gagDef.name, 'gag has a name')
            check(ActionGlobals.getGagDef(track, tier - 1) is gagDef, 'getGagDef lookup %s' % gagDef.name)
            check(gagDef.damage >= 0 and gagDef.heal >= 0, 'non-negative numbers for %s' % gagDef.name)
        check(ActionGlobals.getGagDef(track, 3) is None, 'level 3 does not exist')
        check(ActionGlobals.getGagDef(track, -1) is None, 'negative level does not exist')
    check(ActionGlobals.getGagDef(99, 0) is None, 'unknown track does not exist')

    cake = ActionGlobals.getGagDef(ActionGlobals.THROW_TRACK, 2)
    base = ActionGlobals.getGagDamage(cake, 0)
    mastered = ActionGlobals.getGagDamage(cake, ActionGlobals.MAX_TRACK_XP)
    lured = ActionGlobals.getGagDamage(cake, 0, lured=True)
    check(mastered > base, 'mastery raises damage')
    check(lured > base, 'lure raises Throw damage')
    eq(ActionGlobals.getGagDamage(None, 0), 0, 'no gag means no damage')
    eq(ActionGlobals.getGagDamage(ActionGlobals.getGagDef(ActionGlobals.HEAL_TRACK, 0), 0), 0,
       'heal gags do no damage')
    check(ActionGlobals.getGagDamage(cake, 0, damageMultiplierPercent=50) < base,
          'percentage multiplier lowers damage')
    soaked = ActionGlobals.getGagDamage(ActionGlobals.getGagDef(ActionGlobals.DROP_TRACK, 2), 0, soaked=True)
    plain = ActionGlobals.getGagDamage(ActionGlobals.getGagDef(ActionGlobals.DROP_TRACK, 2), 0)
    check(soaked > plain, 'soaked raises Drop damage')
    sound = ActionGlobals.getGagDef(ActionGlobals.SOUND_TRACK, 0)
    check(ActionGlobals.getGagDamage(sound, 0, soaked=True) > ActionGlobals.getGagDamage(sound, 0),
          'soaked raises Sound damage')

    approx(ActionGlobals.getMasteryFraction(0), 0.0, 'mastery fraction at 0')
    approx(ActionGlobals.getMasteryFraction(ActionGlobals.MAX_TRACK_XP), 1.0, 'mastery fraction at max')
    approx(ActionGlobals.getMasteryFraction(-5), 0.0, 'mastery fraction floors')
    approx(ActionGlobals.getMasteryFraction(ActionGlobals.MAX_TRACK_XP * 5), 1.0, 'mastery fraction caps')

    eq(ActionGlobals.getNextTierXpRequirement(1), ActionGlobals.TRACK_TIER_XP[1], 'tier II xp gate')
    eq(ActionGlobals.getNextTierCost(2), ActionGlobals.TRACK_TIER_COST[2], 'tier III cost')
    check(ActionGlobals.getNextTierXpRequirement(3) is None, 'no gate past max tier')
    check(ActionGlobals.getNextTierCost(3) is None, 'no cost past max tier')

    eq(ActionGlobals.canPurchaseNextTier(0, 9999, 9999), (False, 'undiscovered'), 'cannot buy hidden track')
    eq(ActionGlobals.canPurchaseNextTier(1, 499, 99999), (False, 'xp'), 'xp gate')
    eq(ActionGlobals.canPurchaseNextTier(1, 500, 10), (False, 'beans'), 'bean gate')
    check(ActionGlobals.canPurchaseNextTier(1, 500, 1250)[0], 'purchase allowed')
    eq(ActionGlobals.canPurchaseNextTier(3, 9999, 99999), (False, 'maxed'), 'maxed gate')

    eq(ActionGlobals.getTrackTierFromAccess(0), 0, 'access 0 is undiscovered')
    eq(ActionGlobals.getTrackTierFromAccess(2), 2, 'access 2 is tier II')
    eq(ActionGlobals.getTrackTierFromAccess(8), 3, 'legacy access 8 maps to max')
    eq(ActionGlobals.getTrackTierFromAccess('2'), 2, 'string access parses')
    eq(ActionGlobals.getTrackTierFromAccess(None), 0, 'None access is undiscovered')
    eq(ActionGlobals.getTrackTierFromAccess('junk'), 0, 'junk access is undiscovered')
    eq(ActionGlobals.getTrackTierFromAccess(-3), 0, 'negative access clamps to 0')

    heal = ActionGlobals.getGagDef(ActionGlobals.HEAL_TRACK, 2)
    check(heal.heal > 0 and heal.style == ActionGlobals.GAG_STYLE_HEAL, 'toon-up heals')
    trap = ActionGlobals.getGagDef(ActionGlobals.TRAP_TRACK, 2)
    check(trap.style == ActionGlobals.GAG_STYLE_TRAP and trap.duration > 0, 'trap has a lifetime')
    lure = ActionGlobals.getGagDef(ActionGlobals.LURE_TRACK, 2)
    check(lure.style == ActionGlobals.GAG_STYLE_LURE and lure.duration > 0, 'lure has a duration')


# ---------------------------------------------------------------------------
# Progression
# ---------------------------------------------------------------------------
def testProgression():
    toon = FakeToon([1, 0, 0, 2, 3, 1, 0], [100, 0, 0, 1500, 5000, 20, 0], [4, 5, -1],
                    maxHp=40, money=5000)
    eq(ActionProgression.getTrackTiers(toon), [1, 0, 0, 2, 3, 1, 0], 'track tiers')
    eq(ActionProgression.getDiscoveredTracks(toon), [0, 3, 4, 5], 'discovered tracks')
    eq(ActionProgression.getUndiscoveredTracks(toon), [1, 2, 6], 'undiscovered tracks')
    eq(ActionProgression.getEquippedTracks(toon), [4, 5], 'equipped tracks drop empty slots')
    eq(ActionProgression.getTrackXpList(toon), [100, 0, 0, 1500, 5000, 20, 0], 'xp list')
    eq(ActionProgression.getTrackXp(toon, 4), 5000, 'xp for a track')
    eq(ActionProgression.getTrackXp(toon, 99), 0, 'xp for an unknown track')

    check(ActionProgression.isTrackDiscovered(toon, 4), 'throw discovered')
    check(not ActionProgression.isTrackDiscovered(toon, 1), 'trap not discovered')
    check(ActionProgression.isTrackEquipped(toon, 4), 'throw equipped')
    check(not ActionProgression.isTrackEquipped(toon, 3), 'sound not equipped')
    eq(ActionProgression.getMaxLevelForTrack(toon, 4), 2, 'throw maxes at level 2')
    eq(ActionProgression.getMaxLevelForTrack(toon, 1), -1, 'undiscovered track has no level')
    check(ActionProgression.canUseGag(toon, 4, 2), 'throw III usable')
    check(not ActionProgression.canUseGag(toon, 4, 3), 'throw IV unusable')
    check(not ActionProgression.canUseGag(toon, 3, 0), 'sound not equipped')
    check(not ActionProgression.canUseGag(toon, 1, 0), 'trap not discovered')

    eq(ActionProgression.normalizeLoadout([3, 3, 1, 4, 5, 0], toon), [3, 4, 5],
       'normalize drops dupes, hidden and extra tracks')
    eq(ActionProgression.normalizeLoadout([], toon), [-1, -1, -1], 'empty loadout pads')
    eq(ActionProgression.normalizeLoadout([4, 'x', 5], toon), [4, 5, -1], 'junk entries are dropped')
    eq(ActionProgression.normalizeLoadout([9, 4], toon), [4, -1, -1], 'out-of-range entries are dropped')
    eq(ActionProgression.normalizeLoadout([4, 5, 3], None), [4, 5, 3], 'no toon trusts the caller')
    eq(ActionProgression.normalizeLoadout([0, 0, 4], None), [0, 4, -1], 'duplicates dropped without a toon')

    allowed, reason, cost, xpNeeded = ActionProgression.getPurchaseState(toon, 3)
    check(allowed and cost == 4000 and xpNeeded == 1400, 'sound III purchasable')
    toon.money = 2000
    allowed, reason, cost, xpNeeded = ActionProgression.getPurchaseState(toon, 3)
    check(not allowed and reason == 'beans', 'sound III blocked by beans')
    toon.money = 5000
    allowed, reason, cost, xpNeeded = ActionProgression.getPurchaseState(toon, 0)
    check(not allowed and reason == 'xp', 'toon-up needs mastery first')
    allowed, reason, cost, xpNeeded = ActionProgression.getPurchaseState(toon, 4)
    check(not allowed and reason == 'maxed', 'throw III cannot be upgraded')

    eq(ActionProgression.getDefaultLoadout(toon)[:2], [4, 5], 'default loadout prefers throw/squirt')
    blank = FakeToon([1, 0, 0, 0, 1, 1, 0], [0] * 7, [])
    eq(ActionProgression.getDefaultLoadout(blank)[:2], [4, 5], 'default loadout from a blank toon')
    eq(ActionProgression.getDefaultLoadout(FakeToon([0] * 7, [0] * 7, [])), [-1, -1, -1],
       'no discovered tracks means an empty loadout')

    toon.damageMultiplier = 150
    eq(ActionProgression.getDamageMultiplierPercent(toon), 150, 'damage multiplier read')
    eq(ActionProgression.getDamageMultiplierPercent(object()), 100, 'missing multiplier defaults to 100')
    cake = ActionGlobals.getGagDef(ActionGlobals.THROW_TRACK, 1)
    check(ActionProgression.getGagDamageForToon(toon, cake) > cake.damage,
          'toon gag damage includes mastery and multiplier')
    eq(ActionProgression.getGagDamageForToon(toon, None), 0, 'no gag, no damage')
    check(ActionProgression.getPowerRating(toon) > ActionGlobals.MIN_POWER, 'power rating grows with the kit')


# ---------------------------------------------------------------------------
# Rewards
# ---------------------------------------------------------------------------
def testRewards():
    weak = ActionGlobals.getPowerRating([0, 0, 0, 0, 1, 1, 0], [0] * 7, [4, 5], 15)
    strong = ActionGlobals.getPowerRating([3] * 7, [ActionGlobals.MAX_TRACK_XP] * 7, [4, 5, 6], 137)
    calm = ActionGlobals.getDifficultyProfile(1, weak, 0)
    strongCalm = ActionGlobals.getDifficultyProfile(10, strong, 0)
    invasion = ActionGlobals.getDifficultyProfile(10, strong, ActionGlobals.PRESSURE_STAGE_THRESHOLDS[5])
    for level in range(1, 13):
        check(ActionGlobals.getKillBeans(level, invasion) > ActionGlobals.getKillBeans(level, calm),
              'invasion pays more beans at level %d' % level)
        check(ActionGlobals.getKillBeans(level, calm, elite=True) > ActionGlobals.getKillBeans(level, calm),
              'elites pay more beans at level %d' % level)
        check(ActionGlobals.getKillXp(level, invasion) > ActionGlobals.getKillXp(level, strongCalm),
              'invasion pays more xp at level %d' % level)
        check(ActionGlobals.getKillXp(level, calm, elite=True) > ActionGlobals.getKillXp(level, calm),
              'elites pay more xp at level %d' % level)
        check(ActionGlobals.getHitXp(10, level, calm) >= 1, 'hit xp is positive at level %d' % level)
        check(ActionGlobals.getKillBeans(level, calm) >= 1, 'bean floor at level %d' % level)
    check(ActionGlobals.isTrivialTarget(1, strong), 'level 1 is trivial for a maxed toon')
    check(not ActionGlobals.isTrivialTarget(12, weak), 'level 12 is never trivial')
    check(ActionGlobals.getKillXp(1, ActionGlobals.getDifficultyProfile(1, strong, 0))
          < ActionGlobals.getKillXp(1, ActionGlobals.getDifficultyProfile(1, weak, 0)),
          'trivial targets pay less xp')


# ---------------------------------------------------------------------------
# Cog roles
# ---------------------------------------------------------------------------
def testCogRoles():
    profile = ActionGlobals.getDifficultyProfile(5, ActionGlobals.MIN_POWER, 0)
    for suitKey in SuitBattleGlobals.getAllRegisteredSuits():
        attributes = SuitBattleGlobals.getSuitAttributes(suitKey)
        role = ActionGlobals.getCogRole(suitKey, attributes.tier)
        check(role in ActionGlobals.ROLE_TUNING, 'role for %s' % suitKey)
        check(ActionGlobals.getCogSpeed(role, profile) > 0, 'speed for %s' % suitKey)
    for name, expected in ActionGlobals.ROLE_OVERRIDES.items():
        eq(ActionGlobals.getCogRole(name, 0), expected, 'override role for %s' % name)
    eq(ActionGlobals.getCogRole('f', 0), ActionGlobals.ROLE_BY_TIER[0], 'flunky uses the tier table')
    eq(ActionGlobals.getCogRole('unknown', 7), ActionGlobals.ROLE_BY_TIER[7], 'unknown suits fall back to tier')
    eq(ActionGlobals.getCogRole('unknown', 99), ActionGlobals.ROLE_BY_TIER[-1], 'tier clamps at the top')


# ---------------------------------------------------------------------------
# Objectives
# ---------------------------------------------------------------------------
def testObjectives():
    generator = ObjectiveGenerator.ObjectiveGenerator(5, (1, 2, 3), (25, 25, 25, 25), [4, 5, 3],
                                                       rng=random.Random(7))
    objectives = generator.generate()
    check(1 <= len(objectives) <= ActionGlobals.OBJECTIVES_PER_RUN, 'objective count')
    eq(objectives[0].kind, ActionGlobals.OBJ_DEFEAT_ANY, 'first objective is a plain defeat')
    eq(len(set(obj.kind for obj in objectives)), len(objectives), 'objective kinds are unique')
    for objective in objectives:
        wire = objective.toWire()
        eq(Objective.fromWire(wire).toWire(), wire, 'objective wire round-trip')
        check(objective.target > 0 and objective.beans > 0, 'objective numbers are positive')

    seen = set()
    for tier in range(ActionGlobals.MIN_TIER, ActionGlobals.MAX_TIER + 1):
        for seed in range(40):
            rng = random.Random(seed * 31 + tier)
            gen = ObjectiveGenerator.ObjectiveGenerator(tier, (1, 2, 3), (25, 25, 25, 25), [4, 5, 3], rng=rng)
            for objective in gen.generate():
                seen.add(objective.kind)
                check(objective.target > 0, 'generated target positive')
                check(objective.param >= 0, 'generated param non-negative')
    for kind in range(ActionGlobals.NUM_OBJECTIVE_KINDS):
        check(kind in seen, 'objective kind %d can be generated' % kind)

    low = ObjectiveGenerator.ObjectiveGenerator(1, (1,), (0, 0, 0, 0), [], rng=random.Random(1))
    kinds = [obj.kind for obj in low.generate()]
    check(ActionGlobals.OBJ_REACH_STAGE not in kinds, 'tier 1 has no stage objective')
    check(ActionGlobals.OBJ_DEFEAT_ELITE not in kinds, 'tier 1 has no elite objective')
    noCache = low.generate(allowCache=False)
    check(ActionGlobals.OBJ_FIND_CACHE not in [obj.kind for obj in noCache], 'cache objectives can be disabled')
    healOnly = ObjectiveGenerator.ObjectiveGenerator(5, (1,), (25, 25, 25, 25), [0, 2], rng=random.Random(2))
    check(ActionGlobals.OBJ_HITS_WITH_TRACK not in [obj.kind for obj in healOnly.generate()],
          'toon-up / lure never ask for hits')

    objective = Objective(ActionGlobals.OBJ_DODGE, 0, 3, 100)
    check(not objective.addProgress() and not objective.addProgress() and objective.addProgress(),
          'progress completes on the third dodge')
    check(objective.complete and not objective.addProgress(), 'no progress after completion')
    eq(objective.progress, 3, 'progress clamps at the target')
    check(objective.setProgress(99) is False, 'setProgress on a done objective is a no-op')
    other = Objective(ActionGlobals.OBJ_REACH_STAGE, 4, 1, 100)
    check(other.setProgress(2), 'setProgress completes a stage objective')
    check(Objective.fromWire([ActionGlobals.OBJ_DEFEAT_ANY, 0, 5, 5, 50]).complete,
          'fromWire marks complete objectives')

    check(ObjectiveGenerator.contractClearedBeans(5) > ObjectiveGenerator.contractClearedBeans(1),
          'higher tiers pay a bigger contract bonus')
    check(ObjectiveGenerator.objectiveXp(objectives[0], 5) > ObjectiveGenerator.objectiveXp(objectives[0], 1),
          'higher tiers pay more objective xp')


def testObjectiveText():
    for kind in range(ActionGlobals.NUM_OBJECTIVE_KINDS):
        text = describeObjective(Objective(kind, 0, 3, 50))
        check(isinstance(text, str) and text, 'objective kind %d has text' % kind)
    check('Sellbot' in describeObjective(Objective(ActionGlobals.OBJ_DEFEAT_DEPT, 3, 3, 50)),
          'department name is rendered')
    check('ALERT' in describeObjective(Objective(ActionGlobals.OBJ_REACH_STAGE, ActionGlobals.PRESSURE_ALERT, 1, 50)),
          'stage name is rendered')
    check('Throw' in describeObjective(Objective(ActionGlobals.OBJ_HITS_WITH_TRACK, ActionGlobals.THROW_TRACK, 3, 50)),
          'track name is rendered')


# ---------------------------------------------------------------------------
# Attack registry
# ---------------------------------------------------------------------------
def testAttackRegistry():
    shapes = (CogAttackRegistry.SHAPE_MELEE, CogAttackRegistry.SHAPE_PROJECTILE,
              CogAttackRegistry.SHAPE_AOE, CogAttackRegistry.SHAPE_BEAM)
    for attack in SuitAttackType:
        if attack == SuitAttackType.NO_ATTACK:
            continue
        realtime = CogAttackRegistry.getRealtimeAttack(attack)
        check(realtime is not None, 'realtime def for %s' % attack.name)
        check(realtime.shape in shapes, 'known shape for %s' % attack.name)
        check(realtime.maxRange > 0 and realtime.windup > 0, 'usable timing for %s' % attack.name)
        eq(realtime.attackId, int(attack), 'attack id matches the enum for %s' % attack.name)
        previous = realtime
        for level in range(1, CogAttackRegistry.MAX_MUTATION + 1):
            mutated = CogAttackRegistry.mutate(realtime, level)
            check(mutated.windup <= previous.windup or realtime.windup <= 0.3,
                  'mutation shortens windup for %s at %d' % (attack.name, level))
            check(mutated.recovery <= previous.recovery or realtime.recovery <= 0.2,
                  'mutation shortens recovery for %s at %d' % (attack.name, level))
            check(mutated.cooldown <= previous.cooldown or realtime.cooldown <= 0.8,
                  'mutation shortens cooldown for %s at %d' % (attack.name, level))
            check(mutated.windup >= 0.3 and mutated.recovery >= 0.2 and mutated.cooldown >= 0.8,
                  'mutation floors hold for %s at %d' % (attack.name, level))
            previous = mutated
        top = CogAttackRegistry.mutate(realtime, CogAttackRegistry.MAX_MUTATION)
        check(top.moveDuringRecovery, 'max mutation allows moving during recovery for %s' % attack.name)
        check(CogAttackRegistry.mutate(realtime, 0) is realtime, 'zero mutation is identity')
        check(CogAttackRegistry.mutate(realtime, 99).moveDuringRecovery, 'mutation clamps high')
        eq(CogAttackRegistry.mutate(realtime, -3).windup, realtime.windup, 'mutation clamps low')
        if realtime.shape in (CogAttackRegistry.SHAPE_AOE, CogAttackRegistry.SHAPE_PROJECTILE):
            check(top.pulses >= 2, 'max mutation adds a pulse for %s' % attack.name)
        if realtime.shape == CogAttackRegistry.SHAPE_PROJECTILE:
            check(realtime.travelTime(20.0) > 0, 'projectile travel time for %s' % attack.name)
        else:
            eq(realtime.travelTime(20.0), 0.0, 'non-projectile travel time for %s' % attack.name)

    stamped = SuitAttackType.RUBBER_STAMP
    check(CogAttackRegistry.getRealtimeAttackById(int(stamped)) is CogAttackRegistry.getRealtimeAttack(stamped),
          'attack lookup by id')
    check(CogAttackRegistry.getRealtimeAttackById(999) is None, 'unknown attack id')
    eq(CogAttackRegistry.getLongestRange([]), 0.0, 'no attacks means no range')

    for suitKey in SuitBattleGlobals.getAllRegisteredSuits():
        attributes = SuitBattleGlobals.getSuitAttributes(suitKey)
        for mutation in range(CogAttackRegistry.MAX_MUTATION + 1):
            attackSet = CogAttackRegistry.buildAttackSet(attributes, mutation)
            eq(len(attackSet), len(attributes.attacks), 'attack set for %s at mutation %d' % (suitKey, mutation))
            longest = CogAttackRegistry.getLongestRange(attackSet)
            for distance in (2.0, 8.0, 15.0, longest * 0.9):
                pick = CogAttackRegistry.chooseAttack(attackSet, distance, random.Random(1))
                check(pick is None or pick[0].minRange <= distance <= pick[0].maxRange * 1.15,
                      'chooseAttack range for %s at %.1f' % (suitKey, distance))
            check(CogAttackRegistry.chooseAttack(attackSet, longest * 0.5, random.Random(1)) is not None,
                  '%s has an attack at half range' % suitKey)
            check(CogAttackRegistry.chooseAttack([], 1.0) is None, 'empty attack set picks nothing')
            tooFar = CogAttackRegistry.chooseAttack(attackSet, longest * 100.0, random.Random(1),
                                                    allowFallback=False)
            check(tooFar is None, '%s cannot attack out of range with fallback off' % suitKey)


# ---------------------------------------------------------------------------
# Cog combat controller
# ---------------------------------------------------------------------------
def _makeController(name='f', toonPos=(0, 0, 0), cogPos=(6, 0, 0), profile=None):
    planner = FakePlanner(FakeAir())
    suit = FakeSuit(planner=planner, level=5, name=name, pos=cogPos)
    toon = FakeToon(pos=toonPos, maxHp=100, hp=100)
    director = FakeDirector(toons=[toon], profile=profile)
    controller = CogCombatControllerAI(suit, director)
    controller.pos = Point3(cogPos)
    controller.havePos = True
    director.controllerList = [controller]
    return controller, suit, toon, director


def testControllerStates():
    profile = ActionGlobals.getDifficultyProfile(5, ActionGlobals.MIN_POWER, 0)
    controller, suit, toon, director = _makeController(profile=profile)
    eq(controller.state, ActionGlobals.COG_PATROL, 'fresh controller patrols')
    check(controller.isActive() and not controller.isEngaged(), 'fresh controller is active, not engaged')
    eq(controller.getTargetId(), 0, 'fresh controller has no target')

    controller.tick(0.0, 0.1, profile)
    check(suit.actionControlled, 'patrol acquires a nearby toon')
    eq(controller.state, ActionGlobals.COG_ALERT, 'acquire enters ALERT')
    eq(suit.actionState, ActionGlobals.COG_ALERT, 'client sees the ALERT state')
    check(any(name == 'smPosHpr' for name, _ in suit.updates), 'alert forces a position update')

    controller.tick(ActionGlobals.COG_ALERT_DURATION + 0.1, 0.1, profile)
    eq(controller.state, ActionGlobals.COG_ENGAGE, 'alert transitions to ENGAGE')
    eq(suit.actionState, ActionGlobals.COG_ENGAGE, 'client sees the ENGAGE state')
    check(controller.isEngaged(), 'engage counts as engaged')

    # Force an attack and verify the telegraph + resolution.
    now = ActionGlobals.COG_ALERT_DURATION + 0.2
    controller.pos = Point3(0, 0, 0)
    suit.pos = Point3(0, 0, 0)
    toon.pos = Point3(4, 0, 0)
    controller.nextAttackTime = 0.0
    controller._tickEngage(now, 0.1, profile)
    check(controller.currentAttack is not None, 'an in-range Cog starts an attack')
    check(any(name == 'actionAttack' for name, _ in suit.updates), 'attack is telegraphed')
    resolveTime = controller.currentAttack['resolveTime']
    lockedHeading = controller.currentAttack['facing']
    originalPos = Point3(toon.pos)
    toon.pos = Point3(-4, 3, 0)
    controller._tickEngage(now + 0.01, 0.01, profile)
    eq(controller.heading, lockedHeading, 'wind-up facing stays locked when the Toon strafes')
    toon.pos = originalPos
    controller.tick(resolveTime + 0.01, 0.1, profile)
    check(toon.damageTaken > 0, 'a Toon standing still takes the hit')
    check(any(name == 'actionAttackResolved' for name, _ in suit.updates), 'attack resolution is broadcast')
    check(controller.currentAttack is None, 'the attack returns to the cooldown')
    check(controller.nextAttackTime > resolveTime, 'the next attack is put on cooldown')

    # Heavy hits stagger; lure and soak are tracked statuses.
    controller2, suit2, toon2, director2 = _makeController(profile=profile)
    controller2.tick(0.0, 0.1, profile)
    controller2.tick(ActionGlobals.COG_ALERT_DURATION + 0.1, 0.1, profile)
    drop = ActionGlobals.getGagDef(ActionGlobals.DROP_TRACK, 2)
    controller2.onDamaged(toon2, drop, 5, 1.3)
    eq(controller2.state, ActionGlobals.COG_STAGGER, 'a heavy track staggers the Cog')
    check(controller2.hasStatus(ActionGlobals.STATUS_STAGGER, 1.35), 'stagger status is set')
    controller2.tick(controller2.stateUntil + 0.1, 0.1, profile)
    eq(controller2.state, ActionGlobals.COG_ENGAGE, 'stagger ends back in ENGAGE')

    squirt = ActionGlobals.getGagDef(ActionGlobals.SQUIRT_TRACK, 0)
    controller2.onDamaged(toon2, squirt, 1, 5.0)
    check(controller2.isSoaked(5.1), 'squirt soaks the Cog')
    check(not controller2.isSoaked(5.0 + ActionGlobals.SOAKED_DURATION + 1.0), 'soak expires')

    lure = ActionGlobals.getGagDef(ActionGlobals.LURE_TRACK, 0)
    controller2.applyLure(toon2, lure, 20.0)
    eq(controller2.state, ActionGlobals.COG_LURED, 'lure enters the LURED state')
    check(controller2.isLured(20.1), 'lured status is active')
    controller2.tick(20.2, 0.1, profile)
    check(controller2.isLured(20.2), 'lure persists while ticking')
    check(controller2.getTargetId() == toon2.doId, 'a lured Cog keeps its target')
    # A lured Cog cannot be staggered out of the lure.
    controller2.onDamaged(toon2, drop, 999, 20.3)
    eq(controller2.state, ActionGlobals.COG_LURED, 'lure resists staggering')

    controller2.onDefeated()
    eq(controller2.state, ActionGlobals.COG_DEFEATED, 'defeat state')
    check(not controller2.alive and not controller2.isActive(), 'defeated controller is inactive')

    # Departing when the target is lost.
    controller3, suit3, toon3, director3 = _makeController(profile=profile)
    controller3.tick(0.0, 0.1, profile)
    controller3.tick(ActionGlobals.COG_ALERT_DURATION + 0.1, 0.1, profile)
    director3.toons = []
    controller3.tick(ActionGlobals.COG_ALERT_DURATION + 0.2, 0.1, profile)
    check(controller3.lostTargetSince is not None, 'lost target is tracked')
    check(controller3.getTargetId() == 0, 'the lost target is cleared')
    controller3.tick(controller3.lostTargetSince + ActionGlobals.COG_LOST_TARGET_TIMEOUT + 1.0, 0.1, profile)
    eq(controller3.state, ActionGlobals.COG_DEPARTING, 'a bored Cog departs')
    check(suit3.flewAway, 'departing tells the suit to fly away')

    controller3.cleanup()
    check(controller3.suit is None and controller3.director is None and not controller3.alive, 'cleanup detaches')


def testControllerHitResolution():
    profile = ActionGlobals.getDifficultyProfile(3, ActionGlobals.MIN_POWER, 0)
    for shape, name in ((CogAttackRegistry.SHAPE_MELEE, 'melee'),
                        (CogAttackRegistry.SHAPE_BEAM, 'beam'),
                        (CogAttackRegistry.SHAPE_AOE, 'aoe'),
                        (CogAttackRegistry.SHAPE_PROJECTILE, 'projectile')):
        controller, suit, toon, director = _makeController(profile=profile)
        controller.pos = Point3(0, 0, 0)
        controller.heading = 0.0
        toon.pos = Point3(4, 0, 0)
        realtime = CogAttackRegistry.RealtimeCogAttack(SuitAttackType.RUBBER_STAMP, shape, 0.0, 30.0,
                                                       0.5, 0.5, 2.0, hitRadius=2.2,
                                                       splashRadius=8.0, coneHalfAngle=45.0)
        # A Toon at +X from the Cog lies at heading -90 degrees.
        attack = {'rt': realtime, 'attr': None, 'aim': Point3(4, 0, 0), 'startTime': 0.0,
                  'resolveTime': 0.5, 'pulsesLeft': 1, 'facing': -90.0, 'moveAllowed': False,
                  'damage': 5, 'targetId': toon.doId}
        check(controller._resolveHit(attack, toon), '%s hits a Toon in front of the Cog' % name)
        attack['facing'] = 90.0
        if shape in (CogAttackRegistry.SHAPE_MELEE, CogAttackRegistry.SHAPE_BEAM):
            check(not controller._resolveHit(attack, toon), '%s misses behind the Cog' % name)
        else:
            check(controller._resolveHit(attack, toon), '%s ignores facing' % name)
        if shape != CogAttackRegistry.SHAPE_PROJECTILE:
            attack['facing'] = -90.0
            toon.pos = Point3(500, 0, 0)
            attack['aim'] = Point3(500, 0, 0)
            check(not controller._resolveHit(attack, toon), '%s misses out of range' % name)

    # AoE can be jumped over.
    controller, suit, toon, director = _makeController(profile=profile)
    controller.pos = Point3(0, 0, 0)
    aoe = CogAttackRegistry.RealtimeCogAttack(SuitAttackType.RUBBER_STAMP, CogAttackRegistry.SHAPE_AOE,
                                              0.0, 12.0, 0.5, 0.5, 2.0, splashRadius=8.0)
    attack = {'rt': aoe, 'attr': None, 'aim': Point3(0, 0, 0), 'startTime': 0.0, 'resolveTime': 0.5,
              'pulsesLeft': 1, 'facing': 0.0, 'moveAllowed': False, 'damage': 5, 'targetId': toon.doId}
    toon.pos = Point3(1, 0, 5)
    check(not controller._resolveHit(attack, toon), 'a jumping Toon dodges the ground pulse')
    toon.pos = Point3(1, 0, 0)
    check(controller._resolveHit(attack, toon), 'a grounded Toon is caught by the pulse')

    # Projectiles can be dodged sideways.
    projectile = CogAttackRegistry.RealtimeCogAttack(SuitAttackType.RUBBER_STAMP,
                                                     CogAttackRegistry.SHAPE_PROJECTILE, 0.0, 30.0,
                                                     0.5, 0.5, 2.0, hitRadius=2.0)
    controller.pos = Point3(0, 0, 0)
    attack = {'rt': projectile, 'attr': None, 'aim': Point3(20, 0, 0), 'startTime': 0.0,
              'resolveTime': 0.5, 'pulsesLeft': 1, 'facing': 0.0, 'moveAllowed': False,
              'damage': 5, 'targetId': toon.doId}
    toon.pos = Point3(10, 10, 0)
    check(not controller._resolveHit(attack, toon), 'a Toon who steps aside dodges the projectile')
    toon.pos = Point3(10, 0, -1.5)
    check(controller._resolveHit(attack, toon), 'a Toon on the line of travel is hit')


def testControllerMovement():
    profile = ActionGlobals.getDifficultyProfile(5, ActionGlobals.MIN_POWER, 0)
    controller, suit, toon, director = _makeController(cogPos=(0, 0, 0), toonPos=(30, 0, 0), profile=profile)
    before = Point3(controller.pos)
    controller._move(0.0, 0.5, profile, Point3(30, 0, 0), 30.0)
    check((controller.pos - before).length() > 0.1, 'a distant Cog closes on its target')

    other, otherSuit, _, _ = _makeController(cogPos=(0.5, 0, 0), profile=profile)
    other.state = ActionGlobals.COG_ENGAGE
    other.pos = Point3(0.5, 0, 0)
    controller.director.controllerList = [controller, other]
    controller.state = ActionGlobals.COG_ENGAGE
    controller.pos = Point3(0, 0, 0)
    check(controller._separation(1.0).length() > 0.0, 'overlapping Cogs push apart')

    controller.navPath = []
    controller.navTargetPos = None
    check(controller._nextWaypoint(0.0, Point3(30, 0, 0)) is None, 'no path means no waypoint')

    controller.role = ActionGlobals.ROLE_SUPPRESSOR
    controller.tuning = ActionGlobals.ROLE_TUNING[controller.role]
    controller.pos = Point3(2, 0, 0)
    controller._move(0.0, 0.5, profile, Point3(0, 0, 0), 2.0)
    check(controller.pos.getX() > 2.0, 'a keep-distance Cog backs off')


def testControllerStreaming():
    profile = ActionGlobals.getDifficultyProfile(5, ActionGlobals.MIN_POWER, 0)
    controller, suit, toon, director = _makeController(profile=profile)
    controller.state = ActionGlobals.COG_ENGAGE
    controller.pos = Point3(1.0, 2.0, 3.0)
    controller.heading = 45.0
    controller.lastSendTime = -100.0
    controller.lastSentPos = Point3(999, 999, 999)
    controller._flushPosition(10.0)
    check(any(name == 'smPosHpr' for name, _ in suit.updates), 'position is streamed to clients')
    suit.updates = []
    controller.lastSendTime = 10.0
    controller._flushPosition(10.05)
    check(not any(name == 'smPosHpr' for name, _ in suit.updates), 'position is rate limited')

    suit.updates = []
    controller.stopped = False
    controller.lastSendTime = -100.0
    controller.lastSentPos = Point3(controller.pos)
    controller.lastSentHeading = controller.heading
    controller._flushPosition(20.0)
    check(controller.stopped and any(name == 'smStop' for name, _ in suit.updates),
          'a stationary engaged Cog sends a stop')

    # Patrolling Cogs never stream (the DNA path owns them).
    controller.state = ActionGlobals.COG_PATROL
    suit.updates = []
    controller._flushPosition(30.0, force=True)
    check(not suit.updates, 'a patrolling Cog does not stream positions')


def testControllerPatrolGuards():
    profile = ActionGlobals.getDifficultyProfile(5, ActionGlobals.MIN_POWER, 0)
    controller, suit, toon, director = _makeController(profile=profile)
    director.runActive = False
    controller.tick(0.0, 0.1, profile)
    eq(controller.state, ActionGlobals.COG_PATROL, 'no run means no acquisition')
    director.runActive = True
    suit.legType = SuitLeg.TOff
    director.toons[0].pos = Point3(5, 0, 0)
    controller.tick(0.1, 0.1, profile)
    eq(controller.state, ActionGlobals.COG_PATROL, 'a non-walking Cog never self-acquires, even up close')
    suit.legType = SuitLeg.TWalk
    director.toons[0].pos = Point3(1000, 0, 0)
    controller.tick(0.2, 0.1, profile)
    eq(controller.state, ActionGlobals.COG_PATROL, 'a distant Toon is not noticed')
    director.toons[0].pos = Point3(5, 0, 0)
    controller.tick(0.3, 0.1, profile)
    eq(controller.state, ActionGlobals.COG_ALERT, 'a close Toon is noticed')


def testBuildingRoomCogsNeedDirectorAggro():
    """A planted building Cog fights only when the room director aggro()s it."""
    profile = ActionGlobals.getDifficultyProfile(5, ActionGlobals.MIN_POWER, 0)
    controller, suit, toon, director = _makeController(profile=profile)
    suit.legType = SuitLeg.TOff
    director.runActive = True
    director.toons[0].pos = Point3(2, 0, 0)
    controller.tick(0.0, 0.1, profile)
    eq(controller.state, ActionGlobals.COG_PATROL, 'planted Cog ignores the closest Toon')
    controller.aggro(toon, 0.2)
    check(controller.isEngaged(), 'director aggro engages the room')


def testBuildingDirectorFloorScaling():
    air = FakeAir()
    planner = FakePlanner(air)
    interior = type('FakeInterior', (), {'topFloor': 2})()
    director = BuildingActionDirectorAI(interior, planner)
    toon = FakeToon(pos=(0, 0, 0), maxHp=50, hp=50)
    air.doId2do[toon.doId] = toon
    CLOCK.set(0.0)
    suit = FakeSuit(planner, level=1, name='f', legType=SuitLeg.TOff)
    air.doId2do[suit.doId] = suit
    director.startFloor(1, [toon.doId], [suit], lambda: None)
    check(director.tier > ActionGlobals.DEFAULT_TIER, 'deeper floors scale the effective tier')
    check(director.controllers.get(suit.doId) is not None, 'room Cogs are registered with the director')
    check(controller_engaged(director, suit), 'a room Cog is engaged as the floor opens')
    director._finishFloor()
    check(director.floorFinished, 'finishing the floor marks it done')
    director.stop()
    check(not director.controllers, 'stopping releases the room controllers')


def controller_engaged(director, suit):
    controller = director.controllers.get(suit.doId)
    return controller is not None and controller.isEngaged()


def testEngageSlotLimit():
    profile = ActionGlobals.getDifficultyProfile(1, ActionGlobals.MIN_POWER, 0)
    toon = FakeToon(pos=(0, 0, 0), maxHp=50, hp=50)
    director = FakeDirector(toons=[toon], profile=profile, engageLimit=1)
    controllers = []
    for _ in range(3):
        suit = FakeSuit(FakePlanner(FakeAir()), level=3, name='f', pos=(3.0, 0, 0))
        controller = CogCombatControllerAI(suit, director)
        controller.pos = Point3(suit.pos)
        controller.havePos = True
        controllers.append(controller)
    director.controllerList = controllers
    for controller in controllers:
        controller.tick(0.0, 0.1, profile)
    engaged = [controller for controller in controllers if controller.isEngaged()]
    eq(len(engaged), 1, 'only the engage limit hunts a single Toon at once')


def testControllerRetarget():
    profile = ActionGlobals.getDifficultyProfile(5, ActionGlobals.MIN_POWER, 0)
    controller, suit, toon, director = _makeController(profile=profile)
    controller.tick(0.0, 0.1, profile)
    controller.tick(ActionGlobals.COG_ALERT_DURATION + 0.1, 0.1, profile)
    second = FakeToon(pos=(3, 0, 0), maxHp=50, hp=50)
    director.toons.append(second)
    toon.hp = 0
    controller.tick(ActionGlobals.COG_ALERT_DURATION + 0.3, 0.1, profile)
    eq(controller.getTargetId(), second.doId, 'a Cog retargets when its Toon goes sad')


# ---------------------------------------------------------------------------
# Street director
# ---------------------------------------------------------------------------
def _makeDirector(hoodInfoIdx=0):
    air = FakeAir()
    planner = FakePlanner(air, hoodInfoIdx=hoodInfoIdx)
    director = StreetDirectorAI(planner)
    planner.actionDirector = director
    director.rng = StubRandom()
    toon = FakeToon(pos=(10, 0, 0), maxHp=30, money=0)
    air.doId2do[toon.doId] = toon
    CLOCK.set(0.0)
    return director, planner, air, toon


def testDirectorRunLifecycle():
    director, planner, air, toon = _makeDirector()
    check(not director.isRunActive(), 'no run before a Toon asks')
    director.requestStreetRun(toon.doId, 4)
    check(director.runActive, 'requesting a run starts it')
    eq(director.getTier(), 4, 'tier is remembered')
    check(director.isRunActive(), 'run is active with a Toon present')
    eq(ActionProgression.getTrackTiers(toon)[4], 1, 'starter kit grants Throw I')
    eq(ActionProgression.getTrackTiers(toon)[5], 1, 'starter kit grants Squirt I')
    eq(ActionProgression.getEquippedTracks(toon), [4, 5], 'starter kit equips throw and squirt')
    eq(toon.getMaxMoney(), ActionGlobals.MAX_JELLYBEANS, 'rogue-lite wallet is raised')
    check(director.objectives, 'a contract is generated')
    check(planner.actionUpdates('setActionTier'), 'the tier is replicated')
    check(planner.actionUpdates('setActionPressure'), 'the pressure meter is replicated')
    check(planner.actionUpdates('setActionObjectives'), 'the contract is replicated')
    check(planner.actionUpdates('actionAnnounce'), 'the run start is announced')
    check(director.getBaseLevels() == (1, 2, 3), 'base levels come from the hood info')
    check(director.getDeptWeights() == (25, 25, 25, 25), 'department weights come from the hood info')

    other = FakeToon(pos=(20, 0, 0), maxHp=30)
    air.doId2do[other.doId] = other
    director.requestStreetRun(other.doId, 9)
    eq(director.getTier(), 4, 'a late joiner does not change the tier')
    eq(len(director.activeToons), 2, 'both Toons are active')

    director.leaveStreetRun(other.doId)
    eq(len(director.activeToons), 1, 'leaving removes the Toon')
    director.leaveStreetRun(toon.doId)
    eq(len(director.activeToons), 0, 'the street empties')
    check(director.emptySince is not None, 'the empty grace window starts')

    CLOCK.set(director.emptySince + StreetDirectorAI.RUN_EMPTY_GRACE + 1.0)
    director._tickBody()
    check(not director.runActive, 'a deserted run ends')
    eq(director.objectives, [], 'ending a run clears the contract')


def testDirectorHoodFallback():
    director, planner, air, toon = _makeDirector(hoodInfoIdx=-1)
    eq(director.getBaseLevels(), StreetDirectorAI.DEFAULT_BASE_LEVELS, 'no hood info uses fallback levels')
    eq(director.getDeptWeights(), (25, 25, 25, 25), 'no hood info uses even department weights')
    eq(director.getPopulationTarget(), 3 + director.getProfile().populationBonus,
       'no hood info uses the fallback population')


def testDirectorProfileRefresh():
    director, planner, air, toon = _makeDirector()
    director.requestStreetRun(toon.doId, 3)
    before = director.getProfile()
    toon.trackArray = [3] * 7
    toon.experience = FakeExperience([ActionGlobals.MAX_TRACK_XP] * 7)
    toon.equippedTracks = [4, 5, 6]
    toon.maxHp = 137
    CLOCK.set(StreetDirectorAI.PROFILE_REFRESH_INTERVAL + 0.1)
    director._tickBody()
    after = director.getProfile()
    check(after.playerPower > before.playerPower, 'profile follows Toon power')
    check(after.levelOffset >= before.levelOffset, 'a stronger Toon faces higher levels')


def testDirectorSpawning():
    director, planner, air, toon = _makeDirector()
    director.requestStreetRun(toon.doId, 6)
    director.lastSpawnTime = -1000.0
    director._tickSpawning(0.0)
    eq(len(planner.createdSuits), 1, 'the director spawns a Cog on cadence')
    suit = planner.createdSuits[0]
    check(suit.doId in director.controllers, 'spawned Cogs get a combat controller')
    check(1 <= suit.getActualLevel() <= ActionGlobals.MAX_COG_LEVEL, 'spawn level is legal')
    target = director.getPopulationTarget()
    for _ in range(target + 5):
        director.lastSpawnTime = -1000.0
        director._tickSpawning(0.0)
    check(len(planner.suitList) <= target, 'the population target is respected')

    director.objectives = [Objective(ActionGlobals.OBJ_DEFEAT_DEPT, 3, 5, 100)]
    eq(director._pickSpawnDept(), 's', 'department objectives bias spawn departments')
    director.objectives = [Objective(ActionGlobals.OBJ_DEFEAT_ANY, 0, 5, 100)]
    check(director._pickSpawnDept() is None, 'no bias without a department objective')


def testDirectorEngageSlots():
    director, planner, air, toon = _makeDirector()
    director.requestStreetRun(toon.doId, 1)
    limit = director.getProfile().engageLimit
    controllers = [object() for _ in range(limit + 3)]
    granted = sum(1 for controller in controllers if director.requestEngageSlot(toon.doId, controller))
    eq(granted, limit, 'engage slots respect the profile limit')
    forced = object()
    check(director.requestEngageSlot(toon.doId, forced, force=True), 'forced engages bypass the limit')
    check(director.requestEngageSlot(toon.doId, controllers[0]), 'an existing engagement is idempotent')
    check(not director.requestEngageSlot(9999, object()), 'an unknown Toon gets no slot')
    director.releaseEngageSlot(controllers[0])
    check(controllers[0] not in director.engageSlots.get(toon.doId, set()), 'releasing frees the slot')

    check(director.canAttackToon(toon.doId, 100.0), 'a Toon can be attacked after a gap')
    director.noteAttackStart(toon.doId, 100.0)
    check(not director.canAttackToon(toon.doId, 100.01), 'attack cadence is limited')


def testDirectorGagHitsAndKills():
    director, planner, air, toon = _makeDirector()
    director.requestStreetRun(toon.doId, 3)
    suit = planner.createNewSuit([], [], suitLevel=5)
    controller = director.controllers[suit.doId]
    captured = []
    controller.onDefeated = lambda: captured.append(True)

    cake = ActionGlobals.getGagDef(ActionGlobals.THROW_TRACK, 2)
    xpBefore = ActionProgression.getTrackXp(toon, ActionGlobals.THROW_TRACK)
    director.onGagHit(suit, toon, cake, 15, 1.0)
    check(ActionProgression.getTrackXp(toon, ActionGlobals.THROW_TRACK) > xpBefore, 'landing a hit earns mastery')
    check(bool(toon.expUpdates), 'mastery gains are replicated to the Toon')

    beansBefore = toon.getMoney()
    director.onCogDefeated(suit, toon, cake)
    check(toon.getMoney() > beansBefore, 'a kill pays jellybeans')
    check(captured, 'a kill tells the controller')
    eq(director.kills, 1, 'the kill counter rises')
    check(any(name == 'actionDefeated' for name, _ in suit.updates), 'the kill is broadcast')
    check(director.pressure.getValue() > 0, 'a kill raises Cog Pressure')

    # A kill with no killer is still safe.
    before = toon.getMoney()
    director.onCogDefeated(suit, None, cake)
    eq(toon.getMoney(), before, 'a kill with no Toon pays nobody')
    check(any(args[0] == 0 for args in [update[1] for update in suit.updates
                                        if update[0] == 'actionDefeated']), 'the no-killer kill is broadcast')


def testKillChains():
    director, planner, air, toon = _makeDirector()
    director.requestStreetRun(toon.doId, 3)
    director._progressObjectives = lambda **kwargs: None
    director._maybeSpawnCache = lambda **kwargs: None
    gag = ActionGlobals.getGagDef(ActionGlobals.THROW_TRACK, 0)
    for index in range(9):
        CLOCK.set(float(index))
        suit = planner.createNewSuit([], [], suitLevel=5)
        expected = int(round(ActionGlobals.getKillBeans(suit.getActualLevel(), director.profile, False)
                             * (1 + min(0.25, index * 0.05))))
        director.onCogDefeated(suit, toon, gag)
        reward = [args for name, args in suit.updates if name == 'actionDefeated'][-1]
        eq(reward[1], expected, 'chain %d applies capped server rewards' % (index + 1))
        eq(director.killChains[toon.doId][0], index + 1, 'consecutive defeats advance chain')
    director.onAttackResolved(None, toon, None, 1)
    check(toon.doId not in director.killChains, 'taking damage breaks chain')
    suit = planner.createNewSuit([], [], suitLevel=5)
    director.onCogDefeated(suit, toon, gag)
    eq(director.killChains[toon.doId][0], 1, 'next defeat restarts chain')
    CLOCK.advance(ActionGlobals.KILL_CHAIN_WINDOW + 0.01)
    suit = planner.createNewSuit([], [], suitLevel=5)
    director.onCogDefeated(suit, toon, gag)
    eq(director.killChains[toon.doId][0], 1, 'expired chain cannot grant a bonus')
    director.leaveStreetRun(toon.doId)
    check(toon.doId not in director.killChains, 'leaving clears personal chain state')


def testStreetBreakthroughs():
    director, planner, air, toon = _makeDirector()
    director.requestStreetRun(toon.doId, 4)
    director.kills = ActionGlobals.BREAKTHROUGH_KILLS - 1
    director.nextBreakthroughKill = ActionGlobals.BREAKTHROUGH_KILLS
    beansBefore = toon.getMoney()
    pressureBefore = director.pressure.getValue()
    director._checkBreakthrough()
    eq(toon.getMoney(), beansBefore, 'a breakthrough waits for its exact kill threshold')
    director.kills += 1
    director._checkBreakthrough()
    check(toon.getMoney() > beansBefore, 'a breakthrough pays participating Toons')
    check(director.pressure.getValue() <= pressureBefore, 'a breakthrough relieves pressure')
    check(any(args[0] == ActionGlobals.ANNOUNCE_BREAKTHROUGH
              for args in planner.actionUpdates('actionAnnounce')), 'a breakthrough is announced')
    eq(director.nextBreakthroughKill, ActionGlobals.BREAKTHROUGH_KILLS * 2,
       'the next breakthrough threshold advances')
    paid = toon.getMoney()
    director._checkBreakthrough()
    eq(toon.getMoney(), paid, 'a threshold pays only once')


def testDirectorObjectives():
    director, planner, air, toon = _makeDirector()
    director.requestStreetRun(toon.doId, 5)
    objects = [Objective(ActionGlobals.OBJ_DEFEAT_ANY, 0, 1, 100),
               Objective(ActionGlobals.OBJ_DODGE, 0, 1, 100)]
    director.objectives = objects
    director.objectivesDirty = False
    partial = [Objective(ActionGlobals.OBJ_DEFEAT_ANY, 0, 3, 100)]
    director.objectives = partial
    director._progressObjectives(kill=True)
    eq(partial[0].progress, 1, 'a kill advances a defeat objective')
    check(director.objectivesDirty, 'partial progress marks the contract dirty')
    director.objectives = objects
    beansBefore = toon.getMoney()
    director._progressObjectives(kill=True)
    check(objects[0].complete, 'a kill completes the defeat objective')
    check(toon.getMoney() > beansBefore, 'completing an objective pays beans')
    check(planner.avatarUpdates and planner.avatarUpdates[-1][1] == 'actionObjectiveComplete',
          'objective completion is replicated to the Toon')

    beforeBonus = toon.getMoney()
    director._progressObjectives(dodge=True)
    check(toon.getMoney() > beforeBonus, 'clearing the contract pays a bonus')
    check(any(args[0] == ActionGlobals.ANNOUNCE_CONTRACT_CLEARED
              for args in planner.actionUpdates('actionAnnounce')), 'the clear is announced')
    check(director.objectives and director.objectives is not objects, 'a fresh contract is generated')

    # Progress routing for every kind.
    director.runActive = True
    routed = [Objective(ActionGlobals.OBJ_DEFEAT_DEPT, 3, 1, 50),
              Objective(ActionGlobals.OBJ_DEFEAT_LEVEL, 5, 1, 50),
              Objective(ActionGlobals.OBJ_DEFEAT_ELITE, 0, 1, 50),
              Objective(ActionGlobals.OBJ_FIND_CACHE, 0, 1, 50),
              Objective(ActionGlobals.OBJ_HITS_WITH_TRACK, ActionGlobals.SOUND_TRACK, 1, 50),
              Objective(ActionGlobals.OBJ_REACH_STAGE, ActionGlobals.PRESSURE_ALERT, 1, 50),
              Objective(ActionGlobals.OBJ_KILL_CHAIN, 0, 4, 50)]
    director.objectives = routed
    director._progressObjectives(kill=True, dept='s', level=5, elite=True)
    check(routed[0].complete, 'department kills progress')
    check(routed[1].complete, 'level kills progress')
    check(routed[2].complete, 'elite kills progress')
    director._progressObjectives(cache=True)
    check(routed[3].complete, 'cache objectives progress')
    director._progressObjectives(hitTrack=ActionGlobals.SOUND_TRACK)
    check(routed[4].complete, 'track-hit objectives progress')
    director.objectives = routed
    director._progressObjectives(stage=ActionGlobals.PRESSURE_INVASION)
    check(routed[5].complete, 'stage objectives progress')
    director._progressObjectives(chain=4)
    check(routed[6].complete, 'kill-chain objectives progress')

    # Unmatched events are ignored.
    director.objectives = [Objective(ActionGlobals.OBJ_DEFEAT_DEPT, 0, 2, 50)]
    director._progressObjectives(kill=True, dept='s')
    eq(director.objectives[0].progress, 0, 'unmatched departments are ignored')
    director.objectives = [Objective(ActionGlobals.OBJ_HITS_WITH_TRACK, ActionGlobals.THROW_TRACK, 2, 50)]
    director._progressObjectives(hitTrack=ActionGlobals.SOUND_TRACK)
    eq(director.objectives[0].progress, 0, 'unmatched tracks are ignored')


def testDirectorTrapsAndToonUp():
    director, planner, air, toon = _makeDirector()
    toon.trackArray[ActionGlobals.HEAL_TRACK] = 1
    toon.trackArray[ActionGlobals.TRAP_TRACK] = 1
    toon.trackArray[ActionGlobals.THROW_TRACK] = 1
    toon.trackArray[ActionGlobals.SQUIRT_TRACK] = 1
    toon.equippedTracks = [4, 0, 1]
    toon.hp = 10
    director.requestStreetRun(toon.doId, 2)
    toon.equippedTracks = [4, 0, 1]

    director.requestTrap(toon.doId, 0, 10.5, 0.0, 0.0)
    eq(len(director.traps), 1, 'a trap inside range is deployed')
    check(planner.actionUpdates('actionTrapPlaced'), 'the trap is broadcast')
    director.requestTrap(toon.doId, 0, 10.5, 0.0, 0.0)
    eq(len(director.traps), 1, 'trap cooldown blocks an instant redeploy')
    director.requestTrap(toon.doId, 0, 1000.0, 1000.0, 0.0)
    eq(len(director.traps), 1, 'a trap out of range is rejected')
    director.requestTrap(9999, 0, 10.0, 0.0, 0.0)
    eq(len(director.traps), 1, 'a trap from an inactive Toon is rejected')

    suit = FakeSuit(planner, level=4, name='f', pos=(10.5, 0, 0))
    controller = FakeSuitController(suit)
    controller.pos = Point3(10.5, 0, 0)
    director.controllers[suit.doId] = controller
    director._tickTraps(0.5)
    eq(len(director.traps), 0, 'a Cog stepping on a trap triggers it')
    check(suit.actionDamage, 'the trap damages its victims')
    check(planner.actionUpdates('actionTrapTriggered'), 'the trigger is broadcast')

    CLOCK.set(20.0)
    director.traps = {}
    director.requestTrap(toon.doId, 0, 10.5, 0.0, 0.0)
    eq(len(director.traps), 1, 'the trap can be redeployed after its cooldown')
    trapId = list(director.traps.keys())[0]
    director._tickTraps(director.traps[trapId]['expires'] + 1.0)
    eq(len(director.traps), 0, 'traps expire')

    hpBefore = toon.getHp()
    director.requestToonUp(toon.doId, 0)
    check(toon.getHp() > hpBefore, 'toon-up heals the Toon')
    check(planner.actionUpdates('actionToonUp'), 'the heal is broadcast')
    healed = toon.getHp()
    director.requestToonUp(toon.doId, 0)
    eq(toon.getHp(), healed, 'toon-up respects its cooldown')
    director.requestToonUp(toon.doId, 2)
    eq(toon.getHp(), healed, 'an unowned heal tier is rejected')
    director.requestToonUp(9999, 0)
    eq(toon.getHp(), healed, 'an inactive Toon cannot heal')


def testDirectorLureAndStatus():
    director, planner, air, toon = _makeDirector()
    toon.trackArray[ActionGlobals.LURE_TRACK] = 1
    toon.equippedTracks = [4, 5, 2]
    director.requestStreetRun(toon.doId, 2)
    toon.equippedTracks = [4, 5, 2]
    suit = planner.createNewSuit([], [], suitLevel=4)
    director.controllers.pop(suit.doId, None)
    controller = FakeSuitController(suit)
    director.controllers[suit.doId] = controller
    lure = ActionGlobals.getGagDef(ActionGlobals.LURE_TRACK, 0)
    xpBefore = ActionProgression.getTrackXp(toon, ActionGlobals.LURE_TRACK)
    director.onLure(suit, toon, lure, 1.0)
    check(controller.lured, 'lure applies the status')
    check(ActionProgression.getTrackXp(toon, ActionGlobals.LURE_TRACK) > xpBefore, 'lure earns mastery')
    lured, soaked = director.getStatusForSuit(suit, 1.5)
    check(lured and not soaked, 'status query reflects lure')
    director.onGagHit(suit, toon, lure, 0, 1.6)
    check(controller.damage is not None, 'a damage-free hit still reaches the controller')


def testDirectorCache():
    director, planner, air, toon = _makeDirector()
    director.requestStreetRun(toon.doId, 2)
    director.rng = StubRandom()
    planner.pointMap = {1100: [FakePoint((50, 50, 0))]}
    spawned = []
    director._spawnCache = lambda zoneId, pos: spawned.append((zoneId, pos))
    director.cache = None
    director._maybeSpawnCache(initial=True)
    eq(len(spawned), 1, 'an initial cache can spawn while tracks are undiscovered')

    director.cache = 'fake'
    director._maybeSpawnCache(initial=True)
    eq(len(spawned), 1, 'only one cache exists at a time')
    director.cache = None

    toon.trackArray = [1] * ActionGlobals.NUM_TRACKS
    spawned = []
    director._maybeSpawnCache(initial=True)
    eq(len(spawned), 0, 'no cache spawns when every track is discovered')
    check(not director._anyUndiscoveredTracks(), 'undiscovered tracks are reported correctly')

    toon.trackArray = [0] * ActionGlobals.NUM_TRACKS
    toon.trackArray[ActionGlobals.THROW_TRACK] = 1
    track, beans = director.onCacheGrabbed(toon.doId, toon)
    check(track >= 0 and beans == 0, 'the cache grants an undiscovered track')
    eq(ActionProgression.getTrackTier(toon, track), 1, 'the granted track is owned')
    check(director.cache is None, 'the director forgets the consumed cache')

    toon.trackArray = [1] * ActionGlobals.NUM_TRACKS
    beansBefore = toon.getMoney()
    track, beans = director.onCacheGrabbed(toon.doId, toon)
    eq(track, -1, 'no track is granted when everything is known')
    check(beans > 0 and toon.getMoney() > beansBefore, 'the cache pays consolation beans')

    toon.trackArray = [0] * ActionGlobals.NUM_TRACKS
    toon.trackArray[ActionGlobals.THROW_TRACK] = 1
    director.cache = None
    director.killsSinceCache = ActionGlobals.CACHE_RESPAWN_KILLS
    spawned = []
    director._maybeSpawnCache(initial=False)
    eq(len(spawned), 1, 'cache respawn is kill gated')
    eq(director.killsSinceCache, 0, 'the kill counter resets after a respawn roll')
    director.onCacheDeleted('other')
    check(director.cache is None, 'deleting an unknown cache is safe')

    # No spots means no cache.
    planner.pointMap = {}
    director.cache = None
    spawned = []
    director._maybeSpawnCache(initial=True)
    eq(len(spawned), 0, 'no spawn points means no cache')


def testDirectorDeathAndAnnouncements():
    director, planner, air, toon = _makeDirector()
    director.requestStreetRun(toon.doId, 5)
    director.pressure.setValue(400)
    planner.updates = []
    director._StreetDirectorAI__handleToonDied(toon.doId)
    check(director.pressure.getValue() <= 200, 'going sad halves Cog Pressure')
    check(toon.doId not in director.activeToons, 'a sad Toon is dropped from the run')
    check(any(args[0] == ActionGlobals.ANNOUNCE_TOON_DOWN
              for args in planner.actionUpdates('actionAnnounce')), 'the knock-down is announced')
    check(planner.actionUpdates('setActionPressure'), 'the new pressure is replicated')
    planner.updates = []
    director._StreetDirectorAI__handleToonDied(toon.doId)
    check(not planner.actionUpdates('actionAnnounce'), 'an unknown Toon cannot be knocked down twice')


def testDirectorStageChanges():
    director, planner, air, toon = _makeDirector()
    director.requestStreetRun(toon.doId, 5)
    planner.updates = []
    director.pressure.setValue(ActionGlobals.PRESSURE_STAGE_THRESHOLDS[ActionGlobals.PRESSURE_CRACKDOWN])
    director._onStageChanged()
    check(planner.actionUpdates('setActionPressure'), 'a stage change replicates pressure')


def testDirectorValidationAndDebug():
    director, planner, air, toon = _makeDirector()
    director.requestStreetRun(toon.doId, 2)
    toon.zoneId = 2200  # walked to a different branch
    director._validateToons(1.0)
    check(toon.doId not in director.activeToons, 'a Toon that leaves the branch is dropped')

    director, planner, air, toon = _makeDirector()
    director.requestStreetRun(toon.doId, 2)
    director.debugSetPressure(ActionGlobals.PRESSURE_STAGE_THRESHOLDS[ActionGlobals.PRESSURE_CRACKDOWN])
    eq(director.pressure.getStage(), ActionGlobals.PRESSURE_CRACKDOWN, 'debug pressure sets the stage')
    director.debugSetPressure(0)
    eq(director.pressure.getStage(), ActionGlobals.PRESSURE_CALM, 'debug pressure can reset to calm')
    director.setTier(9)
    eq(director.getTier(), 9, 'setTier updates the tier')
    check(planner.actionUpdates('setActionTier'), 'tier changes are replicated')
    director.setTier(99)
    eq(director.getTier(), ActionGlobals.MAX_TIER, 'setTier clamps')
    director.stop()
    check(director.planner is None, 'stop detaches from the planner')
    check(not director.controllers, 'stop clears controllers')


def testDirectorPlannerShutdownHandoff():
    """A planner teardown must not erase a run before the next street owns it."""
    director, planner, air, toon = _makeDirector()
    air.actionRunHandoffs = {}
    director.requestStreetRun(toon.doId, 7)
    director.pressure.setValue(432)
    planner.actionDirector = director
    air.suitPlanners = {planner.zoneId: planner}

    director.stop()
    state = air.actionRunHandoffs.get(toon.doId)
    check(state is not None, 'planner shutdown stores the active run handoff')
    eq(state.get('tier'), 7, 'planner shutdown preserves the selected tier')
    eq(state.get('pressure'), 432, 'planner shutdown preserves rolling pressure')

    destination = FakePlanner(air, zoneId=1200)
    resumed = StreetDirectorAI(destination)
    destination.actionDirector = resumed
    resumed.requestStreetRun(toon.doId, 1)
    eq(resumed.getTier(), 7, 'the destination street restores the selected tier')
    eq(resumed.pressure.getValue(), 432, 'the destination street restores rolling pressure')


def testDirectorTierChangeRules():
    director, planner, air, toon = _makeDirector()
    director.requestStreetRun(toon.doId, 3)
    other = FakeToon(pos=(5, 0, 0))
    air.doId2do[other.doId] = other
    director.requestStreetRun(other.doId, 8)
    eq(director.getTier(), 3, 'a populated street keeps its tier')
    director.leaveStreetRun(toon.doId)
    fresher = FakeToon(pos=(5, 0, 0))
    air.doId2do[fresher.doId] = fresher
    director.requestStreetRun(fresher.doId, 8)
    eq(director.getTier(), 3, 'a running street keeps its tier')


def testDirectorTicks():
    director, planner, air, toon = _makeDirector()
    director.requestStreetRun(toon.doId, 5)
    director.lastSpawnTime = -1000.0
    director.lastTickTime = 0.0
    for step in range(1, 6):
        CLOCK.set(step * 0.5)
        director._tickBody()
    eq(director.lastTickTime, 2.5, 'the tick advances its clock')
    check(director.pressure.getValue() > 0, 'pressure rises while a Toon is on the street')
    check(planner.actionUpdates('setActionPressure'), 'ticks replicate pressure')
    director._tickBody()
    check(director.lastTickTime >= CLOCK.getFrameTime(), 'repeated ticks are stable')


# ---------------------------------------------------------------------------
# AI-side hit validation
# ---------------------------------------------------------------------------
class _SuitHarness(object):
    """A DistributedSuitBaseAI driven without a full AI repository."""

    def __init__(self, name='f', level=4, pos=(0, 0, 0), hp=None):
        self.toonId = 0
        self.suit = _suitModule.DistributedSuitBaseAI.__new__(_suitModule.DistributedSuitBaseAI)
        suit = self.suit
        suit.air = FakeAir()
        suit.dna = FakeDNA(name)
        suit.sp = FakePlanner(suit.air)
        suit.level = level
        attributes = SuitBattleGlobals.getSuitAttributes(name)
        suit.maxHP = attributes.getBaseMaxHp(level)
        suit.currHP = suit.maxHP if hp is None else hp
        suit.actionDefeated = False
        suit.actionController = None
        suit.immune = 0
        suit.battleTrap = -1
        suit.battleCellIndex = None
        suit.pathState = None
        suit.skele = 0
        suit.pos = Point3(pos)
        suit.freeGagLastHitByToon = {}
        suit._sends = []
        suit.getPos = lambda: Point3(suit.pos)
        suit.sendUpdate = lambda name, args: suit._sends.append((name, list(args)))
        suit.b_setHP = lambda hp: (setattr(suit, 'currHP', hp), suit._sends.append(('setHP', [hp])))[-1]
        suit.getImmuneStatus = lambda: suit.immune
        suit.getSkelecog = lambda: suit.skele
        suit.getDeathEvent = lambda: 'cogDead'
        suit.taskName = lambda suffix: 'suit-%s' % suffix
        suit.air.getAvatarIdFromSender = lambda: self.toonId

    def spawnToon(self, **kwargs):
        toon = FakeToon(**kwargs)
        self.suit.air.doId2do[toon.doId] = toon
        self.toonId = toon.doId
        return toon


def testSuitHitValidation():
    harness = _SuitHarness(name='f', level=3, pos=(0, 0, 0))
    toon = harness.spawnToon(access=[0, 0, 0, 0, 1, 1, 0], xp=[0] * 7, equipped=[4, 5], maxHp=30,
                             pos=(5, 0, 0))
    toon.hp = 30

    harness.suit.requestFreeGagHit(ActionGlobals.SOUND_TRACK, 0)
    check(not harness.suit._sends, 'an unowned track is rejected')

    harness.suit.requestFreeGagHit(ActionGlobals.THROW_TRACK, 2)
    check(not harness.suit._sends, 'an unowned tier is rejected')

    toon.pos = Point3(500, 0, 0)
    harness.suit.requestFreeGagHit(ActionGlobals.THROW_TRACK, 0)
    check(not harness.suit._sends, 'an out-of-range hit is rejected')

    toon.pos = Point3(5, 0, 0)
    harness.suit.requestFreeGagHit(ActionGlobals.THROW_TRACK, 0)
    check(any(name == 'freeGagHit' for name, _ in harness.suit._sends), 'a valid hit is applied')
    check(harness.suit.currHP < harness.suit.maxHP, 'a valid hit removes HP')

    sendsBefore = len(harness.suit._sends)
    harness.suit.requestFreeGagHit(ActionGlobals.THROW_TRACK, 0)
    eq(len(harness.suit._sends), sendsBefore, 'cooldown blocks a spammy second hit')

    toon.trackArray[ActionGlobals.TRAP_TRACK] = 1
    toon.equippedTracks = [4, 5, 1]
    harness.suit.requestFreeGagHit(ActionGlobals.TRAP_TRACK, 0)
    check(not any(name == 'freeGagHit' and args[1] == ActionGlobals.TRAP_TRACK
                  for name, args in harness.suit._sends), 'traps cannot be aimed at a Cog')

    harness.suit.actionDefeated = True
    sendsBefore = len(harness.suit._sends)
    harness.suit.requestFreeGagHit(ActionGlobals.THROW_TRACK, 0)
    eq(len(harness.suit._sends), sendsBefore, 'a defeated Cog is not targetable')
    harness.suit.actionDefeated = False

    harness.suit.currHP = 0
    check(not harness.suit.isActionTargetable(), 'a dead Cog is not targetable')
    harness.suit.currHP = harness.suit.maxHP
    harness.suit.battleCellIndex = 3
    check(not harness.suit.isActionTargetable(), 'a battle-cell Cog is not targetable')
    harness.suit.battleCellIndex = None
    harness.suit.pathState = 2
    check(not harness.suit.isActionTargetable(), 'a Cog flying away is not targetable')
    harness.suit.pathState = 4
    check(not harness.suit.isActionTargetable(), 'a celebrating Cog is not targetable')
    harness.suit.pathState = 0
    harness.suit.immune = 1
    check(not harness.suit.isActionTargetable(), 'an immune Cog is not targetable')
    harness.suit.immune = 0
    check(harness.suit.isActionTargetable(), 'a patrolling Cog is targetable')

    # A Toon already in a turn-based battle cannot free-aim.
    toon.getBattleId = lambda: 7
    sendsBefore = len(harness.suit._sends)
    harness.suit.requestFreeGagHit(ActionGlobals.THROW_TRACK, 0)
    eq(len(harness.suit._sends), sendsBefore, 'a Toon in a classic battle cannot free-aim')


def testSuitDefeatRewards():
    harness = _SuitHarness(name='f', level=2, pos=(0, 0, 0), hp=1)
    toon = harness.spawnToon(access=[0, 0, 0, 0, 1, 1, 0], xp=[0] * 7, equipped=[4, 5], maxHp=30,
                             pos=(5, 0, 0), money=0)
    toon.hp = 30
    cake = ActionGlobals.getGagDef(ActionGlobals.THROW_TRACK, 0)
    harness.suit.applyActionDamage(toon, cake, 999, 1.0, validate=False)
    eq(harness.suit.currHP, 0, 'lethal damage drops HP to zero')
    check(harness.suit.actionDefeated, 'the Cog is marked defeated')
    check(any(name == 'actionDefeated' for name, _ in harness.suit._sends), 'fallback rewards are broadcast')
    check(toon.getMoney() > 0, 'fallback rewards pay beans')
    check(ActionProgression.getTrackXp(toon, ActionGlobals.THROW_TRACK) > 0, 'fallback rewards pay mastery')
    harness.suit.applyActionDamage(toon, cake, 10, 2.0, validate=False)
    eq(harness.suit.currHP, 0, 'a defeated Cog ignores further damage')


def testSuitLureRouting():
    harness = _SuitHarness(name='f', level=3, pos=(0, 0, 0))
    toon = harness.spawnToon(access=[1, 0, 1, 0, 1, 0, 0], xp=[0] * 7, equipped=[4, 2], maxHp=30,
                             pos=(5, 0, 0))
    toon.hp = 30

    class _Director(object):
        def __init__(self):
            self.lured = False

        def validateGagHit(self, suit, toon, gagDef, now):
            return True

        def onLure(self, suit, toon, gagDef, now):
            self.lured = True

        def getStatusForSuit(self, suit, now):
            return False, False

    director = _Director()
    harness.suit.sp.actionDirector = director
    harness.suit.requestFreeGagHit(ActionGlobals.LURE_TRACK, 0)
    check(director.lured, 'lure is routed through the director')


# ---------------------------------------------------------------------------
# Guided tutorial
# ---------------------------------------------------------------------------
def testTutorialState():
    state = TutorialGlobals.ActionTutorialState()
    eq(state.getStep(), TutorialGlobals.STEP_WELCOME, 'tutorial starts at the welcome step')
    eq(state.getTotal(), TutorialGlobals.NUM_STEPS, 'tutorial has seven steps')
    check(state.requiresAdvance(), 'welcome waits for the player to continue')
    check(not state.report(TutorialGlobals.SIGNAL_MOVE, 1), 'the wrong signal cannot clear welcome')
    check(TutorialGlobals.stepIsExplanatory(TutorialGlobals.STEP_WELCOME), 'welcome is explanatory')

    eq(state.getRequirement()[1], TutorialGlobals.SIGNAL_ADVANCE, 'welcome requires advance')
    check(state.report(TutorialGlobals.SIGNAL_ADVANCE, 1), 'continue clears welcome')
    eq(state.getStep(), TutorialGlobals.STEP_MOVE, 'welcome advances to move')
    check(not state.requiresAdvance(), 'move is not explanatory')

    check(state.report(TutorialGlobals.SIGNAL_MOVE, 1), 'walking clears the move step')
    eq(state.getStep(), TutorialGlobals.STEP_FIRE, 'move advances to fire')

    check(not state.report(TutorialGlobals.SIGNAL_KILL, 0), 'a zero-value kill does not clear fire')
    check(state.report(TutorialGlobals.SIGNAL_KILL, 1), 'defeating a practice Cog clears fire')
    eq(state.getStep(), TutorialGlobals.STEP_LOADOUT, 'fire advances to the Gag Album')

    check(not state.report(TutorialGlobals.SIGNAL_LOADOUT, 1), 'one equipped track is not enough')
    eq(state.getValue(TutorialGlobals.SIGNAL_LOADOUT), 1, 'partial loadout progress is remembered')
    check(state.report(TutorialGlobals.SIGNAL_LOADOUT, TutorialGlobals.LOADOUT_TARGET),
          'equipping enough tracks clears the loadout step')
    eq(state.getStep(), TutorialGlobals.STEP_DODGE, 'loadout advances to dodge')

    check(state.report(TutorialGlobals.SIGNAL_DODGE, 1), 'dodging clears the dodge step')
    eq(state.getStep(), TutorialGlobals.STEP_CACHE, 'dodge advances to the cache')
    check(state.report(TutorialGlobals.SIGNAL_CACHE, 1), 'grabbing a cache clears the cache step')
    eq(state.getStep(), TutorialGlobals.STEP_FINISH, 'cache advances to graduation')
    check(state.requiresAdvance(), 'graduation waits for the player')

    check(state.report(TutorialGlobals.SIGNAL_ADVANCE, 1), 'continue finishes the tutorial')
    check(state.isFinished(), 'the state reports finished')
    check(not state.report(TutorialGlobals.SIGNAL_ADVANCE, 1), 'a finished tutorial ignores signals')
    eq(state.cleared, list(range(TutorialGlobals.NUM_STEPS)), 'every step was cleared in order')
    eq(state.toWire(), [TutorialGlobals.NUM_STEPS, TutorialGlobals.NUM_STEPS], 'wire reports completion')
    eq(TutorialGlobals.makeProgressPercent(state), 100, 'completion is 100 percent')
    eq(state.getProgress(), (TutorialGlobals.NUM_STEPS, TutorialGlobals.NUM_STEPS), 'progress at the end')

    fresh = TutorialGlobals.ActionTutorialState()
    check(TutorialGlobals.makeProgressPercent(fresh) < 100, 'a fresh tutorial is not complete')
    eq(TutorialGlobals.getRequirement(99)[1], None, 'unknown steps have no requirement')
    check(not TutorialGlobals.stepIsExplanatory(99), 'unknown steps are not explanatory')
    check(len(TTLocalizer.ActionTutorialStepNames) == TutorialGlobals.NUM_STEPS, 'one name per step')
    check(len(TTLocalizer.ActionTutorialHints) == TutorialGlobals.NUM_STEPS, 'one hint per step')
    for step in range(TutorialGlobals.NUM_STEPS):
        check(TTLocalizer.ActionTutorialStepNames[step], 'step %d has a name' % step)
        check(TTLocalizer.ActionTutorialHints[step], 'step %d has a hint' % step)


# ---------------------------------------------------------------------------
# dc contract
# ---------------------------------------------------------------------------
def testTutorialSession():
    """Drive an ActionTutorialSession through the whole step machine."""
    import toontown.action.tutorial.ActionTutorialManagerAI as TutorialAI

    class _FakeManager(object):
        def __init__(self, air):
            self.air = air
            self.sent = []

        def sendUpdateToAvatarId(self, avId, fieldName, args):
            self.sent.append((fieldName, list(args)))

        def fields(self, name):
            return [args for (field, args) in self.sent if field == name]

    air = FakeAir()
    toon = FakeToon(pos=(0, 0, 0), maxHp=15, money=0)
    air.doId2do[toon.doId] = toon
    manager = _FakeManager(air)
    session = TutorialAI.ActionTutorialSession(manager, toon.doId, 1100)
    # Avoid spawning real distributed objects; the step machine is what is
    # under test here.
    session._spawnPracticeSuit = lambda: None
    session._armDodge = lambda: None
    session._spawnCache = lambda: None

    check(session.start(), 'the session starts for a present Toon')
    eq(ActionProgression.getTrackTiers(toon)[4], 1, 'the tutorial grants Throw I')
    eq(ActionProgression.getEquippedTracks(toon), [4, 5], 'the tutorial equips the starter loadout')
    eq(toon.getMaxMoney(), ActionGlobals.MAX_JELLYBEANS, 'the tutorial raises the wallet')
    check(toon.getMoney() >= TutorialGlobals.STARTER_BEANS, 'the tutorial hands out starter beans')
    eq(manager.fields('setTutorialStep')[-1], [TutorialGlobals.STEP_WELCOME, TutorialGlobals.NUM_STEPS],
       'the welcome step is replicated')

    session.signal(TutorialGlobals.SIGNAL_ADVANCE, 1)
    eq(session.getStep(), TutorialGlobals.STEP_MOVE, 'welcome advances to move')

    toon.pos = Point3(TutorialGlobals.MOVE_START_FORWARD + 2.0, 0, 0)
    session._tickMove(toon)
    eq(session.getStep(), TutorialGlobals.STEP_FIRE, 'walking far enough clears the move step')

    session.onPracticeCogDefeated(None)
    eq(session.getStep(), TutorialGlobals.STEP_LOADOUT, 'defeating the practice Cog clears the fire step')

    toon.equippedTracks = [4, 5, 0]
    session._tickLoadout(toon)
    eq(session.getStep(), TutorialGlobals.STEP_DODGE, 'equipping enough tracks clears the loadout step')

    session.dodgeStart = Point3(0, 0, 0)
    session.dodgeArmedAt = -100.0
    toon.pos = Point3(TutorialGlobals.DODGE_MOVE_DISTANCE + 1.0, 0, 0)
    session._tickDodge(toon)
    eq(session.getStep(), TutorialGlobals.STEP_CACHE, 'moving away clears the dodge step')

    beansBefore = toon.getMoney()
    track, beans = session.onCacheGrabbed(toon.doId, toon)
    check(track >= 0, 'the tutorial cache grants an undiscovered track')
    eq(session.getStep(), TutorialGlobals.STEP_FINISH, 'grabbing the cache clears the cache step')
    check(toon.getMoney() >= beansBefore + TutorialGlobals.FINISH_BEANS, 'graduation pays beans on the final step')

    session.signal(TutorialGlobals.SIGNAL_ADVANCE, 1)
    check(session.isFinished(), 'continuing at the end finishes the tutorial')
    eq(toon.tutorialAck, 1, 'finishing sets tutorialAck')
    eq(manager.fields('tutorialFinished')[-1], [1], 'graduation is reported to the client')

    # A skip finishes without graduation rewards and still acks the tutorial.
    skipper = FakeToon(pos=(0, 0, 0), maxHp=15)
    air.doId2do[skipper.doId] = skipper
    skipSession = TutorialAI.ActionTutorialSession(manager, skipper.doId, 1100)
    skipSession.start()
    skipSession._finish(False)
    check(skipSession.isFinished(), 'skipping finishes the session')
    eq(skipper.tutorialAck, 1, 'skipping still acks the tutorial')
    eq(manager.fields('tutorialFinished')[-1], [0], 'a skip is reported as unsuccessful')


# ---------------------------------------------------------------------------
# dc contract
# ---------------------------------------------------------------------------
def testTutorialWiring():
    """Static checks that the tutorial is actually hooked up where it must be."""

    def read(path):
        with open(os.path.join(ROOT, path), 'r', encoding='utf-8') as handle:
            return handle.read()

    street = read('toontown/town/Street.py')
    check('def isTutorialStreet' in street, 'Street detects tutorial streets')
    check('if self.isTutorialStreet():' in street, 'Street skips the street run on the tutorial street')

    nameshop = read('toontown/makeatoon/NameShop.py')
    check('self.__createAvatar(skipTutorial=False)' in nameshop, 'new Toons default into the tutorial')

    airepo = read('toontown/ai/ToontownAIRepository.py')
    check('ActionTutorialManagerAI' in airepo, 'the AI repository creates the tutorial manager')

    clientrepo = read('toontown/distributed/ToontownClientRepository.py')
    check("'ActionTutorialManager'" in clientrepo, 'the client keeps the tutorial manager alive')

    tutorial = read('toontown/tutorial/TutorialManagerAI.py')
    check('actionTutorialManager' in tutorial, 'the tutorial manager hands off to the action tutorial')
    check('enterAction' in tutorial, 'the tutorial FSM runs an Action state')

    dc = read('astron/dclass/ttap.dc')
    check('dclass ActionTutorialManager' in dc, 'the dc declares the tutorial manager')
    check('from toontown.action.tutorial import ActionTutorialManager/AI' in dc,
          'the dc imports the tutorial manager')

    magic = read('toontown/spellbook/MagicWordIndex.py')
    check('SkipActionTutorial' in magic, 'a magic word can skip the tutorial')

    coach = read('toontown/action/tutorial/ActionTutorialCoach.py')
    check('requestAdvance' not in coach, 'the coach does not talk to the wire directly')
    check("messenger.send('action-tutorial-skip')" in coach, 'the coach can request a skip')
    check("messenger.send('action-tutorial-advance')" in coach, 'the coach can request a continue')

    manager = read('toontown/action/tutorial/ActionTutorialManager.py')
    for field in ('setTutorialStep', 'setTutorialValue', 'tutorialStepComplete', 'tutorialFinished'):
        check('def %s' % field in manager, 'the client handles %s' % field)

    ai = read('toontown/action/tutorial/ActionTutorialManagerAI.py')
    for field in ('requestAdvance', 'requestSkip'):
        check('def %s' % field in ai, 'the AI handles %s' % field)

    tutorialManager = read('toontown/tutorial/TutorialManager.py')
    check("accept('action-tutorial-finished'" in tutorialManager,
          'the client listens for the tutorial finishing')
    check("request('teleportOut'" in tutorialManager,
          'the client teleports out of the tutorial street')
    check("send('stopTutorial')" in tutorialManager,
          'the client hands the private zones back when it is done')

    streetSource = read('toontown/town/TutorialStreet.py')
    check("messenger.send('stopTutorial')" in streetSource,
          'the tutorial street tunnel also ends the tutorial')


def testTutorialExit():
    """Drive the client TutorialManager through every exit path."""
    import toontown.tutorial.TutorialManager as TutorialManagerModule

    class _State(object):
        def __init__(self, name):
            self.name = name

        def getName(self):
            return self.name

    class _FSM(object):
        def __init__(self, current='walk', fail=False):
            self.current = current
            self.fail = fail
            self.requested = []

        def getCurrentState(self):
            return _State(self.current)

        def request(self, name, args=None):
            if self.fail and name == 'teleportOut':
                raise RuntimeError('this state cannot leave right now')
            self.requested.append((name, args))
            self.current = name

    class _Place(object):
        def __init__(self, fsm):
            self.fsm = fsm

    class _PlayGame(object):
        def __init__(self, place):
            self.place = place

        def getPlace(self):
            return self.place

    class _Messenger(object):
        def __init__(self):
            self.sent = []

        def send(self, event, args=None):
            self.sent.append(event)

        def accept(self, *args, **kwargs):
            pass

        def acceptOnce(self, *args, **kwargs):
            pass

        def ignore(self, *args, **kwargs):
            pass

        def ignoreAll(self):
            pass

    class _TaskMgr(object):
        def __init__(self):
            self.delayed = []
            self.removed = []

        def doMethodLater(self, delay, func, name):
            self.delayed.append((delay, func, name))

        def remove(self, name):
            self.removed.append(name)

        def runDelayed(self, limit=6):
            for _ in range(limit):
                pending = list(self.delayed)
                self.delayed = []
                if not pending:
                    return
                for delay, func, name in pending:
                    func(None)

    def makeManager(fsm=None, hasPlace=True, defaultZone=ToontownGlobals.DaisyGardens):
        messenger = _Messenger()
        taskMgr = _TaskMgr()
        place = _Place(fsm if fsm is not None else _FSM()) if hasPlace else None
        base = types.SimpleNamespace(
            cr=types.SimpleNamespace(playGame=_PlayGame(place)),
            localAvatar=types.SimpleNamespace(defaultZone=defaultZone))
        saved = tuple(getattr(builtins, name, None) for name in ('base', 'messenger', 'taskMgr'))
        builtins.base = base
        builtins.messenger = messenger
        builtins.taskMgr = taskMgr
        manager = TutorialManagerModule.TutorialManager(None)
        return manager, messenger, taskMgr, place, saved

    def restore(saved):
        for name, value in zip(('base', 'messenger', 'taskMgr'), saved):
            if value is None:
                try:
                    delattr(builtins, name)
                except AttributeError:
                    pass
            else:
                setattr(builtins, name, value)

    # A normal graduation teleports to the Toon's preferred playground.
    manager, messenger, taskMgr, place, saved = makeManager()
    try:
        finish = manager._TutorialManager__handleActionTutorialFinished
        finish(True)
        requested = [name for (name, args) in place.fsm.requested]
        eq(requested, ['teleportOut'], 'graduation requests a teleport out of the tutorial street')
        status = place.fsm.requested[0][1][0]
        eq(status['hoodId'], ToontownGlobals.DaisyGardens, 'the teleport targets the Toon default zone')
        eq(status['zoneId'], ToontownGlobals.DaisyGardens, 'the teleport targets the playground zone')
        eq(status['where'], 'playground', 'the teleport lands in a playground')
        eq(status['loader'], 'safeZoneLoader', 'the playground uses the safe zone loader')
        eq(status['how'], 'teleportIn', 'the teleport arrives by teleportIn')
        check('stopTutorial' not in messenger.sent,
              'the private zones are kept until the client arrives')
        taskMgr.runDelayed()
        check('stopTutorial' in messenger.sent, 'the private zones are released once the Toon arrives')

        # A second finish for the same run must not teleport twice.
        finish(False)
        eq(len(place.fsm.requested), 1, 'a repeated finish is ignored')
    finally:
        restore(saved)

    # A state that cannot teleport straight out is brought back to walk.
    fsm = _FSM(current='purchase')
    manager, messenger, taskMgr, place, saved = makeManager(fsm=fsm)
    try:
        manager._TutorialManager__handleActionTutorialFinished(True)
        requested = [name for (name, args) in place.fsm.requested]
        eq(requested, ['walk', 'teleportOut'], 'a shop state walks out before teleporting')
    finally:
        restore(saved)

    # A Toon with no usable default zone still lands in Toontown Central.
    for zone in (0, ToontownGlobals.Tutorial):
        manager, messenger, taskMgr, place, saved = makeManager(defaultZone=zone)
        try:
            manager._TutorialManager__handleActionTutorialFinished(True)
            status = place.fsm.requested[0][1][0]
            eq(status['hoodId'], ToontownGlobals.ToontownCentral,
               'default zone %r falls back to Toontown Central' % zone)
        finally:
            restore(saved)

    # A place that refuses to teleport is retried, then released anyway.
    fsm = _FSM(fail=True)
    manager, messenger, taskMgr, place, saved = makeManager(fsm=fsm)
    try:
        manager._TutorialManager__handleActionTutorialFinished(True)
        check(not messenger.sent, 'a failed teleport is retried before giving up')
        taskMgr.runDelayed()
        check('stopTutorial' in messenger.sent, 'a place that never lets go is still released')
    finally:
        restore(saved)

    # With no place at all the tutorial is simply torn down.
    manager, messenger, taskMgr, place, saved = makeManager(hasPlace=False)
    try:
        manager._TutorialManager__handleActionTutorialFinished(True)
        eq(messenger.sent, ['stopTutorial'], 'no place releases the tutorial immediately')
    finally:
        restore(saved)


def testClientPanels():
    """Drive the client-facing UI: tier picker, Gag Album, HUD and the coach.

    The panels are DirectGui, which needs a live ShowBase, so every widget they
    build is replaced with a recording stub.  Everything the panels actually
    *decide* (tier clamping, purchase gating, loadout edits, HUD formatting and
    the stage banner rules) is real code under test here.
    """
    from direct.showbase import MessengerGlobal
    from toontown.action.tutorial import ActionTutorialCoach as CoachModule
    from toontown.action.ui import ActionHUD as ActionHUDModule
    from toontown.action.ui import LoadoutPanel as LoadoutPanelModule
    from toontown.action.ui import TierSelectPanel as TierSelectPanelModule

    realMessenger = MessengerGlobal.messenger

    class _Stub(object):
        """Stands in for any DirectGui widget or NodePath."""

        def __init__(self, *args, **kwargs):
            self.option = dict(kwargs)
            self.text = kwargs.get('text', '')
            self.fg = kwargs.get('fg')
            self.scale = kwargs.get('scale', 1.0)
            self.visible = True
            self.z = 0.0
            self.colorScale = (1, 1, 1, 1)
            self.calls = {}
            self.destroyed = False

        def __setitem__(self, key, value):
            self.option[key] = value

        def __getitem__(self, key):
            return self.option[key]

        def __getattr__(self, name):
            if name.startswith('_'):
                raise AttributeError(name)
            def _noop(*args, **kwargs):
                self.calls[name] = self.calls.get(name, 0) + 1
                return None
            return _noop

        # -- the handful of methods whose effect we assert on -------------
        def setText(self, text):
            self.text = text

        def getText(self):
            return self.text

        def setFg(self, fg):
            self.fg = fg

        def setScale(self, scale):
            self.scale = scale

        def getScale(self):
            return self.scale

        def setZ(self, z):
            self.z = z

        def getZ(self):
            return self.z

        def setColorScale(self, *color):
            self.colorScale = color

        def show(self):
            self.visible = True

        def hide(self):
            self.visible = False

        def destroy(self):
            self.destroyed = True

        def removeNode(self):
            self.destroyed = True

        def isEmpty(self):
            return False

        def attachNewNode(self, *args):
            return _Stub()

        def start(self):
            self.calls['start'] = self.calls.get('start', 0) + 1

        def finish(self):
            self.calls['finish'] = self.calls.get('finish', 0) + 1

        def isStopped(self):
            return False

        def click(self):
            command = self.option.get('command')
            if command is not None:
                command(*(self.option.get('extraArgs') or []))

    class _TaskMgr(object):
        def __init__(self):
            self.added = []
            self.delayed = []
            self.removed = []

        def add(self, func, name=None):
            self.added.append(name)
            return name

        def doMethodLater(self, delay, func, name=None):
            self.delayed.append((delay, func, name))
            return name

        def remove(self, name):
            self.removed.append(name)

    class _Messenger(object):
        def __init__(self):
            self.sent = []

        def send(self, event, args=None):
            self.sent.append((event, list(args) if args else []))

        def events(self):
            return [event for (event, args) in self.sent]

    class _Clock(object):
        def __init__(self):
            self.time = 0.0

        def getFrameTime(self):
            return self.time

        def getDt(self):
            return 1.0 / 60.0

    def patch(module, names, dgg=True):
        saved = {}
        for name in names:
            if hasattr(module, name):
                saved[name] = getattr(module, name)
                setattr(module, name, _Stub)
        if dgg and hasattr(module, 'DGG'):
            saved['DGG'] = module.DGG
            module.DGG = types.SimpleNamespace(FLAT='flat', NORMAL='normal', DISABLED='disabled',
                                               ROUND='round', RAISED='raised')
        return saved

    def unpatch(module, saved):
        for name, value in saved.items():
            setattr(module, name, value)

    WIDGETS = ('DirectFrame', 'DirectButton', 'DirectWaitBar', 'OnscreenText')
    FX = ('Sequence', 'Parallel', 'Wait', 'Func', 'LerpPosInterval', 'LerpScaleInterval',
          'LerpColorScaleInterval', 'LineSegs')

    savedModules = [
        (TierSelectPanelModule, patch(TierSelectPanelModule, WIDGETS)),
        (LoadoutPanelModule, patch(LoadoutPanelModule, WIDGETS)),
        (ActionHUDModule, patch(ActionHUDModule, WIDGETS + FX)),
        (CoachModule, patch(CoachModule, WIDGETS)),
    ]
    fonts = (ToontownGlobals.getSignFont, ToontownGlobals.getInterfaceFont)
    ToontownGlobals.getSignFont = lambda: None
    ToontownGlobals.getInterfaceFont = lambda: None

    toon = FakeToon(access=[1, 2, 1, 0, 1, 0, 0], xp=[60, 10, 0, 0, 0, 0, 0],
                    equipped=[0, 1], maxHp=40, money=250)
    taskMgr = _TaskMgr()
    messenger = _Messenger()
    clock = _Clock()
    base = types.SimpleNamespace(localAvatar=toon, a2dTopLeft=_Stub(), a2dTopRight=_Stub(),
                                 a2dBottomCenter=_Stub(), a2dBottomLeft=_Stub(), cr=None)
    savedBuiltins = tuple(getattr(builtins, name, None)
                          for name in ('base', 'messenger', 'taskMgr', 'globalClock', 'aspect2d', 'render2d'))
    builtins.base = base
    builtins.messenger = messenger
    builtins.taskMgr = taskMgr
    builtins.globalClock = clock
    builtins.aspect2d = _Stub()
    builtins.render2d = _Stub()

    try:
        # ------------------------------------------------------------- tier select
        panel = TierSelectPanelModule.TierSelectPanel('Silly Street', 7, 'tierDone')
        eq(len(panel.tierButtons), ActionGlobals.MAX_TIER - ActionGlobals.MIN_TIER + 1,
           'the tier picker offers every tier')
        for index, button in enumerate(panel.tierButtons):
            eq(button.option['text'], str(index + 1), 'tier button %d is labelled' % (index + 1))
        eq(panel.tier, 7, 'the tier picker keeps the street default')
        power = ActionProgression.getPowerRating(toon)
        profile = ActionGlobals.previewTier(7, power)
        eq(panel.detailText.text, TTLocalizer.ActionTierDetails % {
            'level': profile.levelOffset, 'reward': profile.rewardScale,
            'pop': profile.populationBonus, 'mut': profile.attackMutation},
           'the tier picker previews the profile for this Toon')
        eq(panel.powerText.text, TTLocalizer.ActionTierPower % int(round(power)),
           'the tier picker shows the Toon power rating')
        flavorIndex = min(len(TTLocalizer.ActionTierFlavor) - 1, (7 - 1) // 2)
        eq(panel.flavorText.text, TTLocalizer.ActionTierFlavor[flavorIndex],
           'the tier picker shows the matching flavour line')
        eq(panel.tierButtons[6].scale, 1.15, 'the chosen tier button is highlighted')
        eq(panel.tierButtons[0].scale, 1.0, 'the other tier buttons are not')

        panel._TierSelectPanel__pickTier(99)
        eq(panel.tier, ActionGlobals.MAX_TIER, 'the tier clamps at the top')
        panel._TierSelectPanel__pickTier(-5)
        eq(panel.tier, ActionGlobals.MIN_TIER, 'the tier clamps at the bottom')
        panel._TierSelectPanel__step(1)
        eq(panel.tier, ActionGlobals.MIN_TIER + 1, 'stepping moves one tier')
        panel._TierSelectPanel__pickTier(ActionGlobals.MAX_TIER)
        panel._TierSelectPanel__step(1)
        eq(panel.tier, ActionGlobals.MAX_TIER, 'stepping past the top does nothing')

        realMessenger.send('arrow_left')
        eq(panel.tier, ActionGlobals.MAX_TIER - 1, 'the arrow keys change the tier')
        realMessenger.send('1')
        eq(panel.tier, 1, 'the number keys jump straight to a tier')
        realMessenger.send('0')
        eq(panel.tier, 10, 'the zero key selects tier ten')

        opens = []
        opener = TierSelectPanelModule.TierSelectPanel('Silly Street', 3, 'otherDone',
                                                      loadoutCallback=lambda: opens.append(1))
        opener._TierSelectPanel__openLoadout()
        eq(opens, [1], 'the loadout button opens the Gag Album')
        opener.destroy()

        panel.confirm()
        eq(messenger.sent, [('tierDone', [10])], 'confirming reports the chosen tier')
        check(panel.finished, 'the tier picker locks itself once confirmed')
        panel.confirm()
        eq(len(messenger.sent), 1, 'a second confirm is ignored')
        panel.destroy()
        eq(panel.frame, None, 'destroying the picker releases its frame')

        # ------------------------------------------------------------- Gag Album
        messenger.sent = []
        album = LoadoutPanelModule.LoadoutPanel('loadoutDone')
        eq(len(album.rows), ActionGlobals.NUM_TRACKS, 'the album lists every gag track')
        for track, row in enumerate(album.rows):
            tier = ActionProgression.getTrackTier(toon, track)
            if tier <= 0:
                eq(row['tier'].text, TTLocalizer.ActionLoadoutLocked, 'undiscovered tracks read as locked')
                check(not row['buy'].visible, 'an undiscovered track cannot be bought')
                check(not row['equip'].visible, 'an undiscovered track cannot be equipped')
                eq(row['xpBar']['value'], 0.0, 'an undiscovered track shows no progress')
                continue
            eq(row['tier'].text, TTLocalizer.ActionLoadoutTier % TTLocalizer.ActionRoman[min(tier, 3)],
               'a discovered track shows its roman tier')
            xp = ActionProgression.getTrackXp(toon, track)
            nextXp = ActionGlobals.getNextTierXpRequirement(tier)
            money = toon.getMoney()
            if nextXp is None:
                check(not row['buy'].visible, 'a mastered track cannot be upgraded further')
                eq(row['xpText'].text, TTLocalizer.ActionLoadoutMastery % (xp, ActionGlobals.MAX_TRACK_XP),
                   'a mastered track shows mastery progress')
            else:
                check(row['buy'].visible, 'an upgradable track shows a buy button')
                eq(row['xpText'].text, TTLocalizer.ActionLoadoutXp % (xp, nextXp),
                   'an upgradable track shows tier progress')
                allowed, reason = ActionGlobals.canPurchaseNextTier(tier, xp, money)
                eq(row['buy']['state'] == 'normal', allowed, 'the buy button matches the purchase rule')
                if not allowed and reason == 'xp':
                    eq(row['buy']['text'], TTLocalizer.ActionLoadoutNeedXp % nextXp,
                       'an unearned upgrade explains the XP shortfall')
            if track in toon.getEquippedTracks():
                slot = toon.getEquippedTracks().index(track) + 1
                eq(row['equip']['text'], TTLocalizer.ActionLoadoutUnequip % slot,
                   'an equipped track shows its slot')
            else:
                eq(row['equip']['text'], TTLocalizer.ActionLoadoutEquip,
                   'an unequipped track invites an equip')
        eq(album.beanText.text, TTLocalizer.ActionLoadoutBeans % toon.getMoney(), 'the album shows the wallet')

        album._LoadoutPanel__toggleEquip(2)
        eq(toon.equipRequests[-1], [0, 1, 2], 'equipping sends the new loadout')
        eq(album.pendingLoadout, [0, 1, 2], 'the album shows the pending loadout')
        eq(album._currentLoadout(), [0, 1, 2], 'the pending loadout is preferred until the server answers')
        album._LoadoutPanel__toggleEquip(0)
        eq(toon.equipRequests[-1], [1, 2], 'unequipping sends the shrunken loadout')
        album._LoadoutPanel__toggleEquip(4)
        eq(toon.equipRequests[-1], [1, 2, 4], 'a third track can be equipped')
        album._LoadoutPanel__toggleEquip(3)
        eq(toon.equipRequests[-1], [1, 2, 4], 'a fourth track is refused when the loadout is full')
        eq(len(album._currentLoadout()), ActionGlobals.MAX_EQUIPPED_TRACKS,
           'the loadout never exceeds its slots')

        album._LoadoutPanel__buy(5)
        eq(toon.tierPurchases[-1], 5, 'the buy button requests that track')
        album._LoadoutPanel__handlePurchase(0, 2, True)
        eq(album.statusText.text, TTLocalizer.ActionPurchaseSuccess % (TTLocalizer.ActionTrackNames[0],
                                                                      TTLocalizer.ActionRoman[2]),
           'a successful purchase is confirmed')
        album._LoadoutPanel__handlePurchase(1, 1, False)
        eq(album.statusText.text, TTLocalizer.ActionPurchaseFail % TTLocalizer.ActionTrackNames[1],
           'a rejected purchase is explained')

        toon.money = 10
        album._LoadoutPanel__handleMoneyChange(toon.money)
        eq(album.beanText.text, TTLocalizer.ActionLoadoutBeans % 10, 'the album follows the wallet')

        album.close()
        eq(messenger.events(), ['loadoutDone'], 'closing the album reports it once')
        album.close()
        eq(len(messenger.events()), 1, 'a second close is ignored')
        album.destroy()
        eq(taskMgr.removed, ['loadoutPanelRefresh'], 'destroying the album stops its refresh task')

        # ------------------------------------------------------------- HUD
        messenger.sent = []
        hud = ActionHUDModule.ActionHUD('Silly Street')
        eq(hud.streetText.text, 'SILLY STREET', 'the HUD titles the street')
        eq(len(hud.slotTexts), ActionGlobals.MAX_EQUIPPED_TRACKS, 'the HUD shows one slot per loadout entry')
        eq(hud.killText.text, TTLocalizer.ActionHudKills % 0, 'the HUD starts with no kills')
        eq(hud.tierText.text, TTLocalizer.ActionHudTier % ActionGlobals.DEFAULT_TIER, 'the HUD shows the tier')
        eq(hud.stageText.text, TTLocalizer.ActionStageNames[0], 'the HUD starts on the calm stage')
        eq(len(hud.objectiveTexts), ActionGlobals.OBJECTIVES_PER_RUN, 'the HUD shows every contract slot')
        hud.setStreetName('Ordinary Street')
        eq(hud.streetText.text, 'ORDINARY STREET', 'the HUD can be retitled')

        hud._ActionHUD__handleTier(6)
        eq(hud.tierText.text, TTLocalizer.ActionHudTier % 6, 'the HUD follows the tier')

        for pressure, stage, oldStage in ((0, 0, 0), (340, 3, 2), (9999, 99, 4), (-5, -3, 1)):
            hud._ActionHUD__handlePressure(pressure, stage, oldStage)
            clamped = max(0, min(ActionGlobals.NUM_PRESSURE_STAGES - 1, stage))
            _, fraction = ActionGlobals.getPressureStageFraction(pressure)
            eq(hud.pressureBar['value'], fraction, 'the pressure bar shows value %r' % (pressure,))
            eq(hud.pressureBar['barColor'], ActionGlobals.PRESSURE_STAGE_COLORS[clamped],
               'the pressure bar takes the stage colour')
            eq(hud.stageText.text, TTLocalizer.ActionStageNames[clamped], 'the stage name follows the meter')
            eq(hud.stageText.fg, ActionGlobals.PRESSURE_STAGE_COLORS[clamped], 'the stage name is tinted')
            if clamped != max(0, min(ActionGlobals.NUM_PRESSURE_STAGES - 1, oldStage)) and clamped > 0:
                eq(hud.banner.text, TTLocalizer.ActionStageNames[clamped], 'a stage change raises a banner')
                eq(hud.bannerSub.text, TTLocalizer.ActionStageBanners[clamped], 'the banner explains the stage')
        hud._ActionHUD__handlePressure(100, 2, 1)
        eq(hud.banner.text, TTLocalizer.ActionStageNames[2], 'a rising stage raises a banner')
        hud._ActionHUD__handlePressure(120, 2, 2)
        eq(hud.banner.text, TTLocalizer.ActionStageNames[2], 'an unchanged stage leaves the banner alone')

        pending = types.SimpleNamespace(kind=ActionGlobals.OBJ_DEFEAT_DEPT, param=0, target=4,
                                       progress=1, complete=False)
        done = types.SimpleNamespace(kind=ActionGlobals.OBJ_REACH_STAGE, param=2, target=1,
                                     progress=1, complete=True)
        hud._ActionHUD__handleObjectives([pending, done])
        eq(hud.objectiveTexts[0].text, '%s  1/4' % describeObjective(pending), 'an open contract shows progress')
        eq(hud.objectiveTexts[1].text, '%s  [DONE]' % describeObjective(done), 'a cleared contract is ticked')
        eq(hud.objectiveTexts[1].fg, (0.55, 0.9, 0.55, 0.9), 'a cleared contract turns green')
        for index in range(2, ActionGlobals.OBJECTIVES_PER_RUN):
            eq(hud.objectiveTexts[index].text, '', 'unused contract slots stay empty')

        localId = toon.getDoId()
        hud._ActionHUD__handleCogDefeated(7, localId + 1, 5, 0, 9, 0, 'Throw', 4)
        eq(hud.killText.text, TTLocalizer.ActionHudKills % 0, 'another Toon kill is not counted')
        hud._ActionHUD__handleCogDefeated(7, localId, 5, 0, 9, 1, 'Throw', 4)
        eq(hud.kills, 1, 'a local kill is counted')
        eq(hud.killText.text, TTLocalizer.ActionHudKills % 1, 'the kill counter updates')
        eq(hud.toasts[-1].text, TTLocalizer.ActionXpToast % (9, TTLocalizer.ActionTrackNames[0]),
           'a kill toasts the track experience')

        hud._ActionHUD__handleGagHit(7, 0, 1, 12)
        for actual, expected in zip(hud.hitMarker.colorScale[:3], ActionGlobals.TRACK_COLORS[0]):
            approx(actual, expected, 'the hit marker takes the track colour', tolerance=1e-6)
        hud._ActionHUD__handleGagHit(7, 99, 1, 12)
        eq(tuple(hud.hitMarker.colorScale[:3]), (1, 1, 1), 'an unknown track falls back to white')

        shakes = []
        base.localAvatar.orbitalCamera = types.SimpleNamespace(addShake=lambda amount: shakes.append(amount))
        hud._ActionHUD__handleToonHit(10, 1, 7)
        approx(hud.vignette['frameColor'][3], min(0.75, 0.25 + 10 * 0.02), 'the vignette scales with damage')
        approx(shakes[-1], min(1.0, 0.25 + 10 * 0.03), 'the camera shakes with damage')
        del base.localAvatar.orbitalCamera
        hud._ActionHUD__handleToonHit(10, 1, 7)

        hud._ActionHUD__handleToonDodged(1, 7)
        eq(hud.toasts[-1].text, TTLocalizer.ActionDodged, 'a dodge is celebrated')
        hud._ActionHUD__handleToonUp(localId + 1, 1, 10)
        eq(hud.toasts[-1].text, TTLocalizer.ActionDodged, 'another Toon healing is not toasted')
        hud._ActionHUD__handleToonUp(localId, 1, 10)
        eq(hud.toasts[-1].text, TTLocalizer.ActionToonUpToast % 10, 'the local Toon healing is toasted')
        hud._ActionHUD__handleToonUp(localId, 1, 0)
        eq(hud.toasts[-1].text, TTLocalizer.ActionToonUpToast % 10, 'a zero heal is not toasted')

        hud._ActionHUD__handleCacheOpened(4, 0)
        eq(hud.banner.text, TTLocalizer.ActionNewTrack, 'a new track raises the discovery banner')
        eq(hud.bannerSub.text, TTLocalizer.ActionNewTrackSub % TTLocalizer.ActionTrackNames[4],
           'the banner names the new track')
        hud._ActionHUD__handleCacheOpened(-1, 12)
        eq(hud.toasts[-1].text, TTLocalizer.ActionCacheBeans % 12, 'a bean cache is toasted')
        hud._ActionHUD__handleCacheOpened(-1, 0)
        eq(hud.toasts[-1].text, TTLocalizer.ActionCacheBeans % 12, 'an empty cache is silent')

        hud._ActionHUD__handleAnnounce(ActionGlobals.ANNOUNCE_ELITE_SPAWNED, 0, 3)
        eq(hud.toasts[-1].text, TTLocalizer.ActionEliteSpawned % 3, 'an elite spawn is announced')
        hud._ActionHUD__handleAnnounce(ActionGlobals.ANNOUNCE_CACHE_SPAWNED, 0, 0)
        eq(hud.toasts[-1].text, TTLocalizer.ActionCacheSpawned, 'a cache spawn is announced')
        hud._ActionHUD__handleAnnounce(ActionGlobals.ANNOUNCE_TOON_DOWN, localId + 1, 0)
        eq(hud.toasts[-1].text, TTLocalizer.ActionToonDown, 'another Toon going sad is announced')
        hud._ActionHUD__handleAnnounce(ActionGlobals.ANNOUNCE_TOON_DOWN, localId, 0)
        eq(hud.toasts[-1].text, TTLocalizer.ActionToonDown, 'the local Toon going sad is not toasted')
        hud._ActionHUD__handleAnnounce(ActionGlobals.ANNOUNCE_CONTRACT_CLEARED, 0, 3)
        eq(hud.banner.text, TTLocalizer.ActionContractClearedTitle, 'a cleared contract raises a banner')
        eq(hud.bannerSub.text, TTLocalizer.ActionContractCleared % 3, 'the banner counts the cleared contracts')
        hud.kills = 5
        hud._ActionHUD__handleAnnounce(ActionGlobals.ANNOUNCE_RUN_STARTED, 0, 7)
        eq(hud.kills, 0, 'a new run resets the kill counter')
        eq(hud.banner.text, TTLocalizer.ActionRunStarted % 7, 'a new run is announced with its tier')
        eq(hud.bannerSub.text, TTLocalizer.ActionTierHintBanner, 'the run banner hints at the tier')

        for index in range(9):
            hud.addToast('toast %d' % index, Vec4(1, 1, 1, 1))
        eq(len(hud.toasts), ActionHUDModule.ActionHUD.MAX_TOASTS, 'the toast list is capped')
        eq(hud.toasts[-1].text, 'toast 8', 'the newest toast is kept')
        eq(len(hud.toastTracks), hud.MAX_TOASTS, 'evicted toast intervals are released')

        hud._ActionHUD__handleObjectiveComplete(0, 12, 0, 30)
        eq(hud.toasts[-1].text, TTLocalizer.ActionObjectiveComplete % {
            'beans': 12, 'xp': 30, 'track': TTLocalizer.ActionTrackNames[0]},
           'a cleared objective is toasted')

        toon.equippedTracks = [0, 1, 2]
        hud.refreshLoadout()
        for slot, track in enumerate((0, 1, 2)):
            tier = ActionProgression.getTrackTier(toon, track)
            eq(hud.slotTexts[slot].text,
               '%s %s' % (TTLocalizer.ActionTrackNames[track], TTLocalizer.ActionRoman[min(tier, 3)]),
               'slot %d names its track and tier' % slot)
        eq(hud.loadout, [0, 1, 2], 'the HUD caches the loadout')
        hud._ActionHUD__handleSlotSelected(2, 2, 1)
        eq(hud.selectedSlot, 2, 'the HUD follows the selected slot')
        eq(hud.slotFrames[2].scale, 1.08, 'the selected slot is enlarged')
        eq(hud.slotFrames[0].scale, 1.0, 'the other slots are not')
        toon.equippedTracks = [0]
        hud.refreshLoadout()
        for slot in range(1, ActionGlobals.MAX_EQUIPPED_TRACKS):
            eq(hud.slotTexts[slot].text, TTLocalizer.ActionSlotEmpty, 'an unused slot reads as empty')

        clock.time = 0.0
        hud._ActionHUD__handleSlotFired(0, 0.5)
        hud._ActionHUD__updateTask(None)
        eq(hud.slotCooldownBars[0]['value'], 0.0, 'a freshly fired slot starts its sweep')
        clock.time = 0.25
        hud._ActionHUD__updateTask(None)
        approx(hud.slotCooldownBars[0]['value'], 0.5, 'the cooldown sweep tracks the clock')
        clock.time = 0.5
        hud._ActionHUD__updateTask(None)
        eq(hud.slotCooldownBars[0]['value'], 1.0, 'a finished cooldown reads as ready')
        check(0 not in hud.slotCooldowns, 'a finished cooldown is forgotten')
        hud._ActionHUD__updateTask(None)

        hud._ActionHUD__handleMoneyChange(123)
        eq(hud.beanText.text, TTLocalizer.ActionHudBeans % toon.getMoney(), 'the HUD shows the wallet')
        hud._ActionHUD__handleAnnounce(ActionGlobals.ANNOUNCE_KILL_CHAIN, localId, 7)
        eq(hud.chainText.text, TTLocalizer.ActionChain % (7, 25), 'chain reward display is capped')
        hud._ActionHUD__updateTask(None)
        eq(hud.chainBar['value'], 1.0, 'a defeat refreshes the chain timer')
        hud._ActionHUD__handleToonHit(1, 1, 7)
        hud._ActionHUD__updateTask(None)
        eq(hud.chainText.text, '', 'damage clears the chain display')
        eq(hud.healthText.text, TTLocalizer.ActionLaffReadout % (toon.getHp(), toon.getMaxHp()),
           'the combat health readout uses replicated laff')
        summary = hud.getRunSummary()
        eq(summary['kills'], hud.kills, 'the run report takes the actual local kill count')
        check(summary['beans'] >= 0 and summary['seconds'] >= 0, 'the run report uses safe replicated counters')
        check(summary['pressure'] >= 0, 'the run report records peak pressure')
        hud.destroy()

        # ------------------------------------------------------------- coach
        coach = CoachModule.ActionTutorialCoach()
        eq(coach.step, TutorialGlobals.STEP_WELCOME, 'the coach opens on the welcome step')
        eq(coach.total, TutorialGlobals.NUM_STEPS, 'the coach knows how many steps there are')
        for step in range(TutorialGlobals.NUM_STEPS):
            coach.setStep(step, TutorialGlobals.NUM_STEPS)
            eq(coach.progressText.text,
               TTLocalizer.ActionTutorialProgress % (step + 1, TutorialGlobals.NUM_STEPS),
               'step %d reports its position' % step)
            eq(coach.stepName.text, TTLocalizer.ActionTutorialStepNames[step], 'step %d has a name' % step)
            eq(coach.hint.text, TTLocalizer.ActionTutorialHints[step], 'step %d has a hint' % step)
            eq(coach.progressBar['value'], min(1.0, float(step) / TutorialGlobals.NUM_STEPS),
               'step %d advances the progress bar' % step)
            eq(coach.continueButton.visible, TutorialGlobals.stepIsExplanatory(step),
               'step %d toggles the Continue button' % step)
        check(coach.skipButton.visible, 'the skip button is always offered')
        eq(coach.title.text, TTLocalizer.ActionTutorialTitle, 'the coach has a title')

        coach.setStep(TutorialGlobals.STEP_LOADOUT, TutorialGlobals.NUM_STEPS)
        coach.setValue(2)
        eq(coach.valueText.text, TTLocalizer.ActionTutorialLoadoutValue % (2, TutorialGlobals.LOADOUT_TARGET),
           'the loadout step counts equipped tracks')
        coach.setStep(TutorialGlobals.STEP_DODGE, TutorialGlobals.NUM_STEPS)
        coach.setValue(1)
        eq(coach.valueText.text, TTLocalizer.ActionTutorialDodgeValue % (TutorialGlobals.DODGE_ATTEMPTS - 1),
           'the dodge step counts remaining tries')
        coach.setStep(TutorialGlobals.STEP_FIRE, TutorialGlobals.NUM_STEPS)
        coach.setValue(3)
        eq(coach.valueText.text, '', 'other steps show no counter')
        coach.showStepComplete(TutorialGlobals.STEP_FIRE, 7)
        eq(coach.valueText.text, TTLocalizer.ActionTutorialStepBeans % 7, 'a cleared step pays beans')

        messenger.sent = []
        coach.continueButton.click()
        eq(messenger.events(), ['action-tutorial-advance'], 'the Continue button asks the AI to advance')
        coach.skipButton.click()
        eq(messenger.events(), ['action-tutorial-advance', 'action-tutorial-skip'],
           'the skip button asks the AI to end the tutorial')

        coach.finish(True)
        eq(coach.stepName.text, TTLocalizer.ActionTutorialComplete, 'graduation is celebrated')
        eq(coach.progressBar['value'], 1.0, 'the finished coach is full')
        check(not coach.continueButton.visible and not coach.skipButton.visible,
              'the finished coach hides its buttons')
        eq(coach.hint.text, '', 'the finished coach clears the hint')
        coach.finish(False)
        eq(coach.stepName.text, TTLocalizer.ActionTutorialSkipped, 'a skipped tutorial says so')
        coach.destroy()
        eq(coach.frame, None, 'destroying the coach releases its frame')
    finally:
        ToontownGlobals.getSignFont, ToontownGlobals.getInterfaceFont = fonts
        for module, saved in savedModules:
            unpatch(module, saved)
        for name, value in zip(('base', 'messenger', 'taskMgr', 'globalClock', 'aspect2d', 'render2d'),
                               savedBuiltins):
            if value is None:
                try:
                    delattr(builtins, name)
                except AttributeError:
                    pass
            else:
                setattr(builtins, name, value)


def testTutorialManager():
    """Drive TutorialManagerAI: zones, arrival, skip and teardown."""
    from toontown.tutorial import TutorialManagerAI as ManagerModule

    class _Dclasses(dict):
        def __missing__(self, key):
            return types.SimpleNamespace(name=key)

    class _Air(object):
        def __init__(self):
            self.doId2do = {}
            self.dclassesByName = _Dclasses()
            self.zone = 1000
            self.log = []
            self.senderId = 0
            self.freed = []
            self.responses = []
            self.channels = []
            self.actionTutorialManager = None

        def getAvatarIdFromSender(self):
            return self.senderId

        def allocateZone(self):
            self.zone += 1
            return self.zone

        def deallocateZone(self, zone):
            self.freed.append(zone)

        def getAvatarExitEvent(self, avId):
            return 'toonExited-%d' % avId

        def writeServerEvent(self, *args):
            self.log.append(args)

        def sendUpdateToChannel(self, obj, channelId, field, args):
            self.channels.append(channelId)
            self.responses.append((field, list(args)))

    class _Sessions(object):
        def __init__(self):
            self.started = []
            self.ended = []

        def startSession(self, avId, streetZone):
            self.started.append((avId, streetZone))
            return avId

        def endSession(self, avId):
            self.ended.append(avId)

    air = _Air()
    sessions = _Sessions()
    air.actionTutorialManager = sessions
    manager = ManagerModule.TutorialManagerAI(air)
    air.tutorialManager = manager

    air.senderId = 4321
    manager.requestTutorial()
    zones = manager.avId2fsm[4321].zones
    allocated = {zones.BRANCH, zones.STREET, zones.SHOP, zones.HQ}
    eq(len(allocated), 4, 'the tutorial allocates four private zones')
    eq(str(manager.avId2fsm[4321].state), 'Action', 'the tutorial FSM starts in the action state')
    eq(air.responses[-1], ('enterTutorial', [zones.STREET, zones.STREET, zones.SHOP, zones.HQ]),
       'the client is told which private zones to load')
    check(air.channels[-1] != 0, 'the tutorial update targets the Toon channel')

    # A Toon that arrives is normalised and handed to the action tutorial.
    toon = FakeToon(access=[0] * ActionGlobals.NUM_TRACKS, money=500, hp=100, maxHp=100)
    toon.doId = 4321
    air.doId2do[4321] = toon
    manager.toonArrived()
    eq(sessions.started, [(4321, zones.STREET)], 'the arrival starts the action tutorial')
    eq(toon.quests, [], 'the tutorial wipes the quest log')
    eq(toon.questHistory, [], 'the tutorial wipes the quest history')
    eq(toon.rewardHistory, (0, []), 'the tutorial wipes the reward history')
    eq((toon.hp, toon.maxHp), (15, 15), 'the tutorial resets the Toon laff')
    eq(toon.inventoryUpdates, ['inv-net-string'], 'the tutorial replicates a full inventory')
    eq(toon.experience.getCurrentExperience(), [0] * ActionGlobals.NUM_TRACKS,
       'the tutorial zeroes gag experience')
    eq(toon.expUpdates[-1], [0] * ActionGlobals.NUM_TRACKS, 'the zeroed experience is replicated')

    # A Toon who already acked the tutorial can never replay it.
    toon.tutorialAck = 1
    manager.toonArrived()
    eq(len(sessions.started), 1, 'a replayed arrival does not restart the tutorial')
    check(any(entry[0] == 'suspicious' for entry in air.log), 'a replay attempt is logged')
    check(4321 not in manager.avId2fsm, 'a rejected arrival releases the Toon')
    eq(sessions.ended, [4321], 'a rejected arrival also ends any session')
    toon.tutorialAck = 0

    # Skipping acks the Toon and tells the client.
    air.doId2do[4321] = toon
    manager.requestSkipTutorial()
    eq(toon.tutorialAck, 1, 'skipping acks the tutorial')
    eq(air.responses[-1], ('skipTutorialResponse', [1]), 'skipping is confirmed')
    air.doId2do.pop(4321)
    manager.requestSkipTutorial()
    eq(air.responses[-1], ('skipTutorialResponse', [0]), 'skipping without a Toon is denied')

    # Finishing acks the Toon, ends the session and frees every private zone.
    air.doId2do[4321] = toon
    toon.tutorialAck = 0
    manager.requestTutorial()
    zones = manager.avId2fsm[4321].zones
    manager.toonArrived()
    manager.allDone()
    eq(toon.tutorialAck, 1, 'finishing acks the tutorial')
    eq(sessions.ended, [4321, 4321], 'finishing ends the action tutorial session')
    eq(sorted(air.freed[-4:]), sorted({zones.BRANCH, zones.STREET, zones.SHOP, zones.HQ}),
       'finishing frees the private zones')
    check(4321 not in manager.avId2fsm, 'finishing forgets the Toon')

    # An unexpected disconnect cleans up the same way.
    manager.requestTutorial()
    fsm = manager.avId2fsm[4321]
    manager._TutorialManagerAI__unexpectedExit(4321)
    eq(str(fsm.state), 'Cleanup', 'a disconnect runs the cleanup state')


def testTutorialAssets():
    """The tutorial's practice Cog, client mirror and track cache."""
    from direct.task import Task

    # ``toontown.battle.BattleProps`` builds a prop pool at import time and
    # reads ``base.config``, so give it a stand-in while we import the cache.
    savedBase = getattr(builtins, 'base', None)
    builtins.base = types.SimpleNamespace(config=types.SimpleNamespace(
        GetInt=lambda key, default=0: default, GetFloat=lambda key, default=0.0: default,
        GetString=lambda key, default='': default))
    try:
        from toontown.action import DistributedTrackCache as CacheModule
        from toontown.action import DistributedTrackCacheAI as CacheAIModule
    finally:
        if savedBase is None:
            del builtins.base
        else:
            builtins.base = savedBase
    from toontown.action.tutorial import ActionTutorialManager as ClientManagerModule
    from toontown.action.tutorial import TutorialPracticeSuitAI as PracticeModule
    from toontown.suit import SuitDNA

    # --------------------------------------------------------- client mirror
    coaches = []

    class _Coach(object):
        def __init__(self, step, total):
            self.step = step
            self.total = total
            self.values = []
            self.steps = []
            self.completions = []
            self.finished = None
            self.destroyed = False
            coaches.append(self)

        def setStep(self, step, total):
            self.steps.append((step, total))

        def setValue(self, value):
            self.values.append(value)

        def showStepComplete(self, step, beans):
            self.completions.append((step, beans))

        def finish(self, success):
            self.finished = success

        def destroy(self):
            self.destroyed = True

    sent = []
    updates = []
    savedMessenger = getattr(builtins, 'messenger', None)
    savedCoach = ClientManagerModule.ActionTutorialCoach
    builtins.messenger = types.SimpleNamespace(
        send=lambda event, args=None: sent.append((event, args)))
    ClientManagerModule.ActionTutorialCoach = _Coach
    try:
        manager = ClientManagerModule.ActionTutorialManager(None)
        eq(manager.step, TutorialGlobals.STEP_WELCOME, 'the mirror starts on the welcome step')
        manager.setTutorialStep(TutorialGlobals.STEP_MOVE, TutorialGlobals.NUM_STEPS)
        eq(len(coaches), 1, 'the first replicated step creates the coach')
        eq(coaches[0].steps[-1], (TutorialGlobals.STEP_MOVE, TutorialGlobals.NUM_STEPS),
           'the coach follows the replicated step')
        manager.setTutorialStep(TutorialGlobals.STEP_FIRE, TutorialGlobals.NUM_STEPS)
        eq(len(coaches), 1, 'the coach is reused for later steps')
        eq(coaches[0].steps[-1], (TutorialGlobals.STEP_FIRE, TutorialGlobals.NUM_STEPS),
           'the coach follows the later step')
        manager.setTutorialStep(-4, TutorialGlobals.NUM_STEPS)
        eq(coaches[0].steps[-1][0], 0, 'a nonsense step index is clamped')
        manager.setTutorialStep(99, 0)
        eq(coaches[0].steps[-1], (0, 1), 'clamping uses the reported total')

        manager.setTutorialValue(2)
        eq(coaches[0].values, [2], 'the coach receives the step counter')
        manager.tutorialStepComplete(1, 6)
        eq(coaches[0].completions, [(1, 6)], 'the coach is told about a cleared step')

        manager.sendUpdate = lambda field, args: updates.append((field, list(args)))
        manager.d_requestAdvance()
        manager.d_requestSkip()
        eq(updates, [('requestAdvance', []), ('requestSkip', [])],
           'the mirror forwards the player requests')

        manager.tutorialFinished(1)
        check(coaches[0].finished, 'the coach is told the tutorial succeeded')
        check(coaches[0].destroyed, 'the coach is torn down when the tutorial ends')
        eq(manager.coach, None, 'the mirror forgets the coach')
        eq(sent[-1], ('action-tutorial-finished', [True]), 'the tutorial finish is announced')
        manager.tutorialFinished(0)
        eq(len(sent), 1, 'a repeated finish is ignored')
        manager.setTutorialValue(3)
        eq(len(coaches), 1, 'a finished tutorial never rebuilds the coach')

        late = ClientManagerModule.ActionTutorialManager(None)
        late.tutorialFinished(0)
        eq(sent[-1], ('action-tutorial-finished', [False]), 'a skipped tutorial is announced as such')
    finally:
        ClientManagerModule.ActionTutorialCoach = savedCoach
        if savedMessenger is None:
            del builtins.messenger
        else:
            builtins.messenger = savedMessenger

    # --------------------------------------------------------- practice Cog
    eq(PracticeModule.TutorialPracticeSuitAI.__name__, 'TutorialPracticeSuitAI',
       'the tutorial practice Cog exists')
    check(0 < TutorialGlobals.PRACTICE_COG_HEALTH_FRACTION < 1.0,
          'a practice Cog is deliberately flimsy')
    check(ActionGlobals.MIN_COG_LEVEL <= TutorialGlobals.PRACTICE_COG_LEVEL <= ActionGlobals.MAX_COG_LEVEL,
          'the practice Cog level is legal')
    for name in PracticeModule.PRACTICE_SUIT_NAMES:
        check(name in SuitDNA.suitHeadTypes, 'practice suit %r is a real Cog' % name)
        dna = SuitDNA.SuitDNA()
        dna.newSuit(name)
        eq(dna.name, name, 'practice suit %r builds a valid DNA' % name)

    class _PracticeFake(object):
        def __init__(self, session):
            self.tutorialSession = session

    class _Session(object):
        def __init__(self):
            self.defeats = []

        def onPracticeCogDefeated(self, suit):
            self.defeats.append(suit)

    from toontown.suit import DistributedTutorialSuitAI as TutorialSuitModule
    session = _Session()
    suit = _PracticeFake(session)
    baseCalls = []
    original = TutorialSuitModule.DistributedTutorialSuitAI._onActionDefeated
    TutorialSuitModule.DistributedTutorialSuitAI._onActionDefeated = \
        lambda self, toon, gagDef: baseCalls.append((toon, gagDef))
    try:
        PracticeModule.TutorialPracticeSuitAI._onActionDefeated(suit, 'toon', 'gag')
        # A Cog whose session has already gone away still takes the normal path.
        suit.tutorialSession = None
        PracticeModule.TutorialPracticeSuitAI._onActionDefeated(suit, 'toon', 'gag')
    finally:
        TutorialSuitModule.DistributedTutorialSuitAI._onActionDefeated = original
    eq(baseCalls, [('toon', 'gag'), ('toon', 'gag')], 'the practice Cog keeps the normal defeat path')
    eq(session.defeats, [suit], 'defeating the practice Cog advances the tutorial once')

    # --------------------------------------------------------- track cache
    class _Fake(object):
        pass

    client = _Fake()
    eq(CacheModule.DistributedTrackCache.getSphereRadius(client), ActionGlobals.CACHE_GRAB_RADIUS,
       'the cache grab radius comes from the tunables')
    savedMessenger = getattr(builtins, 'messenger', None)
    builtins.messenger = types.SimpleNamespace(
        send=lambda event, args=None: sent.append((event, args)))
    try:
        CacheModule.DistributedTrackCache.setTrackReward(client, 4, 12)
    finally:
        if savedMessenger is None:
            del builtins.messenger
        else:
            builtins.messenger = savedMessenger
    eq(sent[-1], ('action-cache-opened', [4, 12]), 'opening a cache announces its reward')

    class _CacheAI(object):
        GRAB_DISTANCE = CacheAIModule.DistributedTrackCacheAI.GRAB_DISTANCE
        DELETE_DELAY = CacheAIModule.DistributedTrackCacheAI.DELETE_DELAY

        def __init__(self, air, director, pos=(0, 0, 0)):
            self.air = air
            self.director = director
            self.pos = list(pos)
            self.grabbed = False
            self.rejects = 0
            self.grabs = []
            self.rewards = []
            self.scheduled = []
            self.deleted = False
            self.deleteTasks = 0

        def d_setReject(self):
            self.rejects += 1

        def d_setGrab(self, avId):
            self.grabs.append(avId)

        def sendUpdateToAvatarId(self, avId, field, args):
            if field == 'setTrackReward':
                self.rewards.append((avId, list(args)))

        def taskName(self, name):
            return 'cache-%s' % name

        def isDeleted(self):
            return False

        def requestDelete(self):
            self.deleted = True

        def _DistributedTrackCacheAI__deleteTask(self, task):
            self.deleteTasks += 1
            self.requestDelete()
            return None

    class _Director(object):
        def __init__(self):
            self.grabbed = []

        def onCacheGrabbed(self, avId, toon):
            self.grabbed.append(avId)
            return (3, 0)

        def onCacheDeleted(self, cache):
            self.grabbed.append('deleted')

    class _CacheAir(object):
        def __init__(self):
            self.doId2do = {}
            self.senderId = 0

        def getAvatarIdFromSender(self):
            return self.senderId

    class _Tasks(object):
        def __init__(self):
            self.delayed = []
            self.removed = []

        def doMethodLater(self, delay, func, name):
            self.delayed.append((delay, func, name))

        def remove(self, name):
            self.removed.append(name)

    air = _CacheAir()
    director = _Director()
    cache = _CacheAI(air, director)
    grab = CacheAIModule.DistributedTrackCacheAI.requestGrab

    air.senderId = 99
    grab(cache)
    eq(cache.rejects, 1, 'an unknown Toon cannot grab a cache')
    check(not cache.grabs, 'an unknown Toon never grabs anything')

    toon = FakeToon(pos=(0, 0, 0))
    air.doId2do[toon.doId] = toon
    air.senderId = toon.doId
    cache.grabbed = True
    grab(cache)
    eq(cache.rejects, 2, 'a spent cache rejects a second grab')

    cache.grabbed = False
    toon.pos = Point3(CacheAIModule.DistributedTrackCacheAI.GRAB_DISTANCE + 1, 0, 0)
    grab(cache)
    eq(cache.rejects, 3, 'a Toon out of reach cannot grab a cache')
    toon.pos = Point3(CacheAIModule.DistributedTrackCacheAI.GRAB_DISTANCE, 0, 0)

    savedTasks = getattr(builtins, 'taskMgr', None)
    tasks = _Tasks()
    builtins.taskMgr = tasks
    try:
        grab(cache)
    finally:
        if savedTasks is None:
            del builtins.taskMgr
        else:
            builtins.taskMgr = savedTasks
    eq(cache.grabs, [toon.doId], 'an in-range grab is accepted')
    eq(director.grabbed, [toon.doId], 'the director hands out the reward')
    eq(cache.rewards, [(toon.doId, [3, 0])], 'the grabbing Toon is told what it won')
    eq(tasks.delayed[-1][0], CacheAIModule.DistributedTrackCacheAI.DELETE_DELAY,
       'a spent cache deletes itself after a delay')
    check(cache.grabbed, 'a spent cache remembers that it was grabbed')

    savedTasks = getattr(builtins, 'taskMgr', None)
    builtins.taskMgr = _Tasks()
    try:
        result = CacheAIModule.DistributedTrackCacheAI._DistributedTrackCacheAI__deleteTask(cache, None)
    finally:
        if savedTasks is None:
            del builtins.taskMgr
        else:
            builtins.taskMgr = savedTasks
    check(cache.deleted, 'the delete task removes the cache')
    eq(result, Task.done, 'the delete task finishes')


def testOptionsPageLifecycle():
    """The Options page must survive quitting before the loader lands.

    ``OptionsTabPage.load`` builds its tabs, exit button and frames from
    asynchronous ``loader.loadModel`` callbacks.  Quitting the client inside
    that window used to raise ``AttributeError: exitButton`` while the sticker
    book unloaded, so both the unload path and the late callbacks are driven
    here.
    """

    class _Stub(object):
        def __init__(self, *args, **kwargs):
            self.destroyed = False
            self.shown = 0
            self.hidden = 0
            self.removedNodes = 0

        def destroy(self):
            self.destroyed = True

        def show(self):
            self.shown += 1

        def hide(self):
            self.hidden += 1

        def find(self, *args):
            return _Stub()

        def remove_node(self):
            self.removedNodes += 1

    # Importing the page pulls in SpeedChat and the sticker book, which read the
    # usual ShowBase globals while their class bodies run, so stand those in.
    STUBS = ('base', 'loader', 'render', 'render2d', 'aspect2d', 'hidden', 'camera', 'globalClock')
    savedGlobals = tuple(getattr(builtins, name, None) for name in STUBS)
    node = types.SimpleNamespace(attachNewNode=lambda *args: None, getParent=lambda: None,
                                 isEmpty=lambda: True, node=lambda: None)

    class _SettingsStub(object):
        """Only the class bodies matter here, so anything else is a no-op."""
        defaultSettings = {}

        def getControls(self):
            return {}

        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    class _BaseStub(object):
        settings = _SettingsStub()
        config = _SettingsStub()
        possibleScreenSizes = []

        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    builtins.base = _BaseStub()
    builtins.loader = types.SimpleNamespace(
        loadFont=lambda *args, **kwargs: None, loadModel=lambda *args, **kwargs: None,
        loadTexture=lambda *args, **kwargs: None, loadMusic=lambda *args, **kwargs: None,
        loadSfx=lambda *args, **kwargs: None)
    builtins.render = node
    builtins.render2d = node
    builtins.aspect2d = node
    builtins.hidden = node
    builtins.camera = node
    builtins.globalClock = CLOCK
    try:
        from toontown.shtiker.OptionsPage import OptionsTabPage
    finally:
        for name, value in zip(STUBS, savedGlobals):
            if value is None:
                try:
                    delattr(builtins, name)
                except AttributeError:
                    pass
            else:
                setattr(builtins, name, value)

    # The exact crash: a page that is unloaded before anything finished loading.
    page = OptionsTabPage.__new__(OptionsTabPage)
    page.tabs = {}
    page.options = {}
    page.exitButton = None
    page._unloaded = False
    page.unload()
    check(page._unloaded, 'unloading the options page marks the pending loads dead')
    page.unload()
    check(page.tabs == {} and page.options == {}, 'a second unload is harmless')

    # A fully loaded page releases everything it built.
    tab = _Stub()
    button = _Stub()
    loaded = OptionsTabPage.__new__(OptionsTabPage)
    loaded.tabs = {'Gameplay': tab}
    loaded.options = {'Gameplay': button}
    loaded.exitButton = _Stub()
    loaded._unloaded = False
    exitButton = loaded.exitButton
    loaded.unload()
    check(tab.destroyed, 'unloading destroys the tab buttons')
    check(exitButton.destroyed, 'unloading destroys the exit button')
    check(loaded.exitButton is None, 'the destroyed exit button is forgotten')
    check(loaded.options == {}, 'the option frames are dropped')

    # Loader callbacks that arrive after the unload must not build widgets.
    fake = _Stub()
    fake._unloaded = True
    fake.tabs = {}
    fake.options = {}
    fake.exitButton = None
    fake.tabOptions = {'Gameplay': []}
    fake._parent = None
    fake.updateTabs = lambda: None
    gui = _Stub()
    OptionsTabPage.createExitButton(fake, gui)
    check(fake.exitButton is None, 'a late exit button is not created')
    OptionsTabPage.createTabs(fake, gui)
    check(fake.options == {}, 'late option frames are not created')
    OptionsTabPage.loadTabs(fake, gui)
    check(fake.tabs == {}, 'late tabs are not created')
    eq(gui.removedNodes, 3, 'every late load hands its model back')

    # The live path still builds the exit button and registers the tabs.
    import toontown.shtiker.OptionsPage as OptionsPageModule
    live = OptionsTabPage.__new__(OptionsTabPage)
    live._unloaded = False
    live.tabs = {}
    live.options = {}
    live.exitButton = None
    live.tabOptions = {'Gameplay': []}
    live._parent = None
    live.updateTabs = lambda: None
    savedWidgets = (OptionsPageModule.DirectButton, OptionsPageModule.OptionsScrolledFrame)
    OptionsPageModule.DirectButton = _Stub
    OptionsPageModule.OptionsScrolledFrame = _Stub
    try:
        liveGui = _Stub()
        live.createExitButton(liveGui)
        check(live.exitButton is not None, 'the exit button is built once the model lands')
        check(liveGui.removedNodes, 'the exit button model is released')
        live.createTabs(liveGui)
        check('Gameplay' in live.options, 'a finished load registers its option frame')
        check(liveGui.removedNodes == 2, 'each finished load releases its gui model')
    finally:
        OptionsPageModule.DirectButton, OptionsPageModule.OptionsScrolledFrame = savedWidgets

    # Entering a tab before its frame exists must not raise, and an existing
    # frame is still shown and hidden correctly.
    tabbed = _Stub()
    tabbed.updateTabs = lambda: None
    tabbed.options = {}
    OptionsTabPage._showOptionsTab(tabbed, 'Gameplay')
    OptionsTabPage._hideOptionsTab(tabbed, 'Gameplay')
    frame = _Stub()
    tabbed.options = {'Gameplay': frame}
    OptionsTabPage._showOptionsTab(tabbed, 'Gameplay')
    OptionsTabPage._hideOptionsTab(tabbed, 'Gameplay')
    eq(frame.shown, 1, 'entering a tab shows its frame')
    eq(frame.hidden, 1, 'leaving a tab hides its frame')

    # The unload guard lives on the real class, not just in this test.
    import inspect
    source = inspect.getsource(OptionsTabPage.unload)
    check('self._unloaded = True' in source, 'unload marks the page unloaded')
    check('if self.exitButton is not None:' in source,
          'unload only destroys a loader that actually finished')


def testDcImports():
    """Every ``from X import Y`` in the dc file must resolve for every repo.

    ``ConnectionRepository.readDCFile`` resolves a dc import by handing the
    symbol name to ``__import__`` as a fromlist entry, so a class whose module
    is not named after it raises at startup -- and only on the repository that
    asks for that suffix.  That is how a client-only module can look fine in
    game while the Uberdog refuses to boot, so every line is checked here for
    the client, the AI and the UD spellings.
    """
    import ast
    import importlib.util

    def definedNames(moduleName):
        try:
            spec = importlib.util.find_spec(moduleName)
        except (ImportError, AttributeError, ValueError):
            return None
        if spec is None or spec.origin is None:
            return None
        try:
            with open(spec.origin, 'r', encoding='utf-8') as handle:
                tree = ast.parse(handle.read())
        except (OSError, SyntaxError, UnicodeDecodeError):
            return None
        names = set()
        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    names.add(alias.asname or alias.name)
        return names

    pattern = re.compile(r'^from\s+([\w.]+)((?:/\w+)*)\s+import\s+(.+)$')
    resolutions = 0
    with open(os.path.join(ROOT, 'astron', 'dclass', 'ttap.dc'), 'r', encoding='utf-8') as handle:
        for raw in handle:
            match = pattern.match(raw.strip())
            if match is None:
                continue
            base, moduleSuffix, symbolList = match.groups()
            if moduleSuffix:
                # Those lines name their AI/UD module explicitly, so the plain
                # package convention below does not apply to them.
                continue
            if not base.startswith(('toontown', 'otp', 'direct')):
                continue
            symbols = []
            for item in symbolList.split(','):
                item = item.strip()
                if not item or item == '*':
                    continue
                parts = item.split('/')
                symbols.append((parts[0], [part for part in parts[1:] if part]))
            if not symbols:
                continue
            for repository in ('', 'AI', 'UD'):
                moduleNames = definedNames(base)
                check(moduleNames is not None, 'dc import module %s exists' % base)
                if moduleNames is None:
                    continue
                for symbolName, flags in symbols:
                    expected = symbolName
                    if flags:
                        if repository and repository in flags:
                            expected = symbolName + repository
                        elif repository == 'UD' and 'AI' in flags:
                            expected = symbolName + 'AI'
                    resolutions += 1
                    if expected in moduleNames:
                        continue
                    # ``from package import Name`` also finds a submodule, which
                    # is how the client and AI implementations of one dc class
                    # usually live in ``Name.py`` / ``NameAI.py``.
                    check(definedNames(base + '.' + expected) is not None,
                          'dc %s resolves %s for the %r repository' %
                          (base, expected, repository or 'client'))
    check(resolutions > 400, 'the dc import table was actually walked')


def testDcContract():
    from panda3d.direct import DCFile
    dcFile = DCFile()
    ok = dcFile.read(Filename.fromOsSpecific(os.path.join(ROOT, 'astron', 'dclass', 'ttap.dc')))
    check(ok, 'dc file parses')
    if not ok:
        return
    expected = {
        'DistributedSuitBase': ('setActionState', 'actionAttack', 'actionAttackResolved', 'actionStatus',
                                'actionDefeated', 'requestFreeGagHit', 'freeGagHit', 'setSmPosHpr', 'setSmStop'),
        'DistributedSuitPlanner': ('requestStreetRun', 'leaveStreetRun', 'setActionTier', 'setActionPressure',
                                   'setActionObjectives', 'actionObjectiveComplete', 'actionAnnounce',
                                   'requestActionTrap', 'actionTrapPlaced', 'actionTrapTriggered',
                                   'requestActionToonUp', 'actionToonUp'),
        'DistributedToon': ('setEquippedTracks', 'requestEquipTracks', 'requestBuyGagTier',
                            'gagTierPurchaseResult', 'setTutorialAck'),
        'DistributedTrackCache': ('setTrackReward', 'requestGrab', 'setGrab'),
        'TutorialManager': ('requestTutorial', 'rejectTutorial', 'requestSkipTutorial', 'skipTutorialResponse',
                            'enterTutorial', 'allDone', 'toonArrived'),
        'ActionTutorialManager': ('requestAdvance', 'requestSkip', 'setTutorialStep', 'setTutorialValue',
                                  'tutorialStepComplete', 'tutorialFinished'),
    }
    for className, fields in expected.items():
        dclass = dcFile.getClassByName(className)
        check(dclass is not None, 'dclass %s exists' % className)
        if dclass is None:
            continue
        for fieldName in fields:
            check(dclass.getFieldByName(fieldName) is not None, '%s.%s exists' % (className, fieldName))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
TESTS = (
    testTunables,
    testPressure,
    testProfiles,
    testGags,
    testProgression,
    testRewards,
    testCogRoles,
    testObjectives,
    testObjectiveText,
    testAttackRegistry,
    testControllerStates,
    testControllerHitResolution,
    testControllerMovement,
    testControllerStreaming,
    testControllerPatrolGuards,
    testBuildingRoomCogsNeedDirectorAggro,
    testBuildingDirectorFloorScaling,
    testEngageSlotLimit,
    testControllerRetarget,
    testDirectorRunLifecycle,
    testDirectorHoodFallback,
    testDirectorProfileRefresh,
    testDirectorSpawning,
    testDirectorEngageSlots,
    testDirectorGagHitsAndKills,
    testKillChains,
    testStreetBreakthroughs,
    testDirectorObjectives,
    testDirectorTrapsAndToonUp,
    testDirectorLureAndStatus,
    testDirectorCache,
    testDirectorDeathAndAnnouncements,
    testDirectorStageChanges,
    testDirectorValidationAndDebug,
    testDirectorPlannerShutdownHandoff,
    testDirectorTierChangeRules,
    testDirectorTicks,
    testSuitHitValidation,
    testSuitDefeatRewards,
    testSuitLureRouting,
    testTutorialState,
    testTutorialSession,
    testTutorialWiring,
    testTutorialExit,
    testTutorialManager,
    testTutorialAssets,
    testClientPanels,
    testOptionsPageLifecycle,
    testDcImports,
    testDcContract,
)


if __name__ == '__main__':
    for test in TESTS:
        try:
            test()
            print('ok   %s' % test.__name__)
        except Exception as exc:  # pragma: no cover - diagnostics only
            import traceback
            traceback.print_exc()
            failures.append('%s raised %r' % (test.__name__, exc))
    print('\n%d checks, %d failure(s)' % (_checks[0], len(failures)))
    if failures:
        sys.exit(1)
    print('All street rogue-lite tests passed.')
