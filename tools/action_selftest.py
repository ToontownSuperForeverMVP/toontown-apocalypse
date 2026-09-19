"""Headless self-test for the real-time street combat foundation.

Run from the repository root with the bundled interpreter::

    Panda3D/python/ppython.exe tools/action_selftest.py

It exercises everything that does not need a running ShowBase or AI: balance
formulas, the pressure meter, procedural objectives, the Cog attack registry,
progression helpers and finally parses the dc file so a typo there is caught
before Astron ever sees it.
"""

import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from panda3d.core import Filename, loadPrcFileData  # noqa: E402

loadPrcFileData('action-selftest', 'language english')

from toontown.action import ActionGlobals, ActionProgression  # noqa: E402
from toontown.action.cog import CogAttackRegistry  # noqa: E402
from toontown.action.director.PressureDirector import PressureMeter  # noqa: E402
from toontown.action.objectives.ObjectiveGenerator import Objective, ObjectiveGenerator  # noqa: E402
from toontown.battle import SuitBattleGlobals  # noqa: E402
from toontown.battle.SuitBattleGlobals import SuitAttackType  # noqa: E402

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print('FAIL:', message)


class FakeExperience:
    def __init__(self, values):
        self.experience = list(values)

    def getExp(self, track):
        return self.experience[track]


class FakeToon:
    def __init__(self, access, xp, equipped, maxHp=15, money=0):
        self.trackArray = list(access)
        self.experience = FakeExperience(xp)
        self.equippedTracks = list(equipped)
        self.maxHp = maxHp
        self.money = money

    def getTrackAccess(self):
        return self.trackArray

    def getEquippedTracks(self):
        return self.equippedTracks

    def getMaxHp(self):
        return self.maxHp

    def getMoney(self):
        return self.money


def testPressure():
    for value, expected in ((0, 0), (99, 0), (100, 1), (219, 1), (220, 2), (360, 3), (520, 4), (700, 5), (999, 5)):
        check(ActionGlobals.getPressureStage(value) == expected, 'stage(%d) != %d' % (value, expected))
    meter = PressureMeter(tier=5)
    changed = False
    for _ in range(600):
        changed = meter.tick(1.0, True) or changed
    check(changed and meter.getStage() >= ActionGlobals.PRESSURE_INVASION, 'ten minutes at tier 5 should reach INVASION')
    meter.onToonDied()
    check(meter.getValue() <= ActionGlobals.MAX_PRESSURE * ActionGlobals.PRESSURE_DEATH_MULTIPLIER + 1,
          'death should halve pressure')
    for _ in range(120):
        meter.tick(1.0, False)
    check(meter.getStage() == ActionGlobals.PRESSURE_CALM and meter.getValue() == 0, 'pressure should decay to CALM')


def testProfiles():
    weak = ActionGlobals.getPowerRating([0, 0, 0, 0, 1, 1, 0], [0] * 7, [4, 5], 15)
    strong = ActionGlobals.getPowerRating([3] * 7, [ActionGlobals.MAX_TRACK_XP] * 7, [4, 5, 6], 137)
    check(abs(weak - ActionGlobals.MIN_POWER) < 1.0, 'starter kit power %.1f should be ~MIN_POWER' % weak)
    check(abs(strong - ActionGlobals.MAX_POWER) < 1.0, 'maxed power %.1f should be ~MAX_POWER' % strong)

    lastOffset = -1
    for tier in range(ActionGlobals.MIN_TIER, ActionGlobals.MAX_TIER + 1):
        profile = ActionGlobals.getDifficultyProfile(tier, weak, 0)
        check(profile.levelOffset >= lastOffset, 'level offset must not drop with tier')
        lastOffset = profile.levelOffset
        check(profile.spawnInterval >= 3.0, 'spawn interval floor')
        check(0.0 < profile.eliteChance <= 0.6, 'elite chance range')
    weakTen = ActionGlobals.getDifficultyProfile(10, weak, 0)
    strongTen = ActionGlobals.getDifficultyProfile(10, strong, 0)
    check(strongTen.levelOffset > weakTen.levelOffset, 'tier 10 must be harder for a strong Toon')
    check(weakTen.levelOffset >= 2, 'tier 10 must still bite for a weak Toon')
    invasion = ActionGlobals.getDifficultyProfile(10, strong, ActionGlobals.PRESSURE_STAGE_THRESHOLDS[5])
    check(invasion.rewardScale > strongTen.rewardScale, 'pressure must raise rewards')
    check(invasion.attackMutation == CogAttackRegistry.MAX_MUTATION, 'tier 10 invasion should max mutations')
    for level in range(1, 13):
        check(ActionGlobals.getKillBeans(level, invasion, True) > ActionGlobals.getKillBeans(level, weakTen), 'elite/invasion beans')
        check(ActionGlobals.getHitXp(10, level, weakTen) >= 1, 'hit xp positive')


def testGags():
    for track in ActionGlobals.ALL_TRACKS:
        defs = ActionGlobals.GAG_DEFS[track]
        check(len(defs) == ActionGlobals.MAX_TRACK_TIER, 'track %d needs three tiers' % track)
        for tier, gagDef in enumerate(defs, start=1):
            check(gagDef.tier == tier and gagDef.level == tier - 1, 'tier/level mismatch %s' % gagDef.name)
            check(ActionGlobals.getGagDef(track, tier - 1) is gagDef, 'getGagDef lookup')
            check(gagDef.cooldown > 0, 'cooldown for %s' % gagDef.name)
        check(ActionGlobals.getGagDef(track, 3) is None, 'level 3 must not exist')
    cake = ActionGlobals.getGagDef(ActionGlobals.THROW_TRACK, 2)
    base = ActionGlobals.getGagDamage(cake, 0)
    mastered = ActionGlobals.getGagDamage(cake, ActionGlobals.MAX_TRACK_XP)
    lured = ActionGlobals.getGagDamage(cake, 0, lured=True)
    check(mastered > base and lured > base, 'mastery and lure must raise cake damage')
    check(ActionGlobals.canPurchaseNextTier(1, 499, 99999) == (False, 'xp'), 'xp gate')
    check(ActionGlobals.canPurchaseNextTier(1, 500, 10) == (False, 'beans'), 'bean gate')
    check(ActionGlobals.canPurchaseNextTier(1, 500, 1250)[0], 'purchase allowed')
    check(ActionGlobals.canPurchaseNextTier(3, 9999, 99999) == (False, 'maxed'), 'maxed gate')
    check(ActionGlobals.getTrackTierFromAccess(8) == 3, 'legacy access 8 maps to tier 3')


def testProgression():
    toon = FakeToon([1, 0, 0, 2, 3, 1, 0], [100, 0, 0, 1500, 5000, 20, 0], [4, 5, -1], maxHp=40, money=5000)
    check(ActionProgression.getDiscoveredTracks(toon) == [0, 3, 4, 5], 'discovered tracks')
    check(ActionProgression.getUndiscoveredTracks(toon) == [1, 2, 6], 'undiscovered tracks')
    check(ActionProgression.getEquippedTracks(toon) == [4, 5], 'equipped tracks')
    check(ActionProgression.canUseGag(toon, 4, 2) and not ActionProgression.canUseGag(toon, 4, 3), 'throw tier III usable')
    check(not ActionProgression.canUseGag(toon, 3, 0), 'sound not equipped')
    check(ActionProgression.normalizeLoadout([3, 3, 1, 4, 5, 0], toon) == [3, 4, 5], 'normalize drops dupes/undiscovered/extra')
    allowed, reason, cost, xpNeeded = ActionProgression.getPurchaseState(toon, 3)
    check(allowed and cost == 4000 and xpNeeded == 1400, 'sound tier III purchasable with 1500 mastery / 5000 beans')
    toon.money = 2000
    allowed, reason, cost, xpNeeded = ActionProgression.getPurchaseState(toon, 3)
    check(not allowed and reason == 'beans', 'sound tier III blocked by beans')
    allowed, reason, cost, xpNeeded = ActionProgression.getPurchaseState(toon, 0)
    check(not allowed and reason == 'xp', 'toon-up needs mastery first')
    check(ActionProgression.getDefaultLoadout(toon)[:2] == [4, 5], 'default loadout prefers throw/squirt')


def testObjectives():
    rng = random.Random(7)
    for tier in range(1, 11):
        generator = ObjectiveGenerator(tier, (1, 2, 3), (0, 0, 25, 75), [4, 5, 3], rng=rng)
        objectives = generator.generate()
        check(1 <= len(objectives) <= ActionGlobals.OBJECTIVES_PER_RUN, 'objective count tier %d' % tier)
        check(objectives[0].kind == ActionGlobals.OBJ_DEFEAT_ANY, 'first objective is a plain defeat')
        kinds = [obj.kind for obj in objectives]
        check(len(kinds) == len(set(kinds)), 'objective kinds unique')
        for objective in objectives:
            wire = objective.toWire()
            back = Objective.fromWire(wire)
            check(back.toWire() == wire, 'wire roundtrip')
            check(objective.target > 0 and objective.beans > 0, 'objective target/beans positive')
    objective = Objective(ActionGlobals.OBJ_DODGE, 0, 3, 100)
    check(not objective.addProgress() and not objective.addProgress() and objective.addProgress(), 'progress completes on third')
    check(objective.complete and not objective.addProgress(), 'no progress after completion')


def testAttackRegistry():
    for attack in SuitAttackType:
        if attack == SuitAttackType.NO_ATTACK:
            continue
        realtime = CogAttackRegistry.getRealtimeAttack(attack)
        check(realtime is not None and realtime.maxRange > 0 and realtime.windup > 0, 'realtime def for %s' % attack.name)
        mutated = CogAttackRegistry.mutate(realtime, CogAttackRegistry.MAX_MUTATION)
        check(mutated.windup < realtime.windup or realtime.windup <= 0.3, 'mutation shortens windup for %s' % attack.name)
        check(mutated.moveDuringRecovery, 'max mutation allows moving during recovery')
    for suitKey in SuitBattleGlobals.getAllRegisteredSuits():
        attributes = SuitBattleGlobals.getSuitAttributes(suitKey)
        attackSet = CogAttackRegistry.buildAttackSet(attributes, 0)
        check(len(attackSet) == len(attributes.attacks), 'attack set for %s' % suitKey)
        longest = CogAttackRegistry.getLongestRange(attackSet)
        for distance in (2.0, 8.0, 15.0, longest * 0.9):
            pick = CogAttackRegistry.chooseAttack(attackSet, distance, random.Random(1))
            check(pick is None or pick[0].minRange <= distance <= pick[0].maxRange * 1.15,
                  'chooseAttack range for %s at %.1f' % (suitKey, distance))
        check(CogAttackRegistry.chooseAttack(attackSet, longest * 0.5, random.Random(1)) is not None,
              '%s should have an attack at half its longest range' % suitKey)
        role = ActionGlobals.getCogRole(suitKey, attributes.tier)
        check(role in ActionGlobals.ROLE_TUNING, 'role for %s' % suitKey)


def testDcFile():
    from panda3d.direct import DCFile
    dcFile = DCFile()
    ok = dcFile.read(Filename.fromOsSpecific(os.path.join(ROOT, 'astron', 'dclass', 'ttap.dc')))
    check(ok, 'dc file must parse')
    if ok:
        for className, fields in (('DistributedSuitBase', ('setActionState', 'actionAttack', 'actionAttackResolved',
                                                           'actionStatus', 'actionDefeated', 'setSmPosHpr')),
                                  ('DistributedSuitPlanner', ('requestStreetRun', 'leaveStreetRun', 'setActionTier',
                                                              'setActionPressure', 'setActionObjectives',
                                                              'actionObjectiveComplete', 'requestActionTrap',
                                                              'actionTrapPlaced', 'requestActionToonUp')),
                                  ('DistributedToon', ('setEquippedTracks', 'requestEquipTracks', 'requestBuyGagTier',
                                                       'gagTierPurchaseResult')),
                                  ('DistributedTrackCache', ('setTrackReward', 'requestGrab', 'setGrab'))):
            dclass = dcFile.getClassByName(className)
            check(dclass is not None, 'dclass %s' % className)
            if dclass is None:
                continue
            for fieldName in fields:
                check(dclass.getFieldByName(fieldName) is not None, '%s.%s' % (className, fieldName))


if __name__ == '__main__':
    for test in (testPressure, testProfiles, testGags, testProgression, testObjectives, testAttackRegistry, testDcFile):
        try:
            test()
            print('ok   %s' % test.__name__)
        except Exception as exc:  # pragma: no cover - diagnostics only
            import traceback
            traceback.print_exc()
            failures.append('%s raised %r' % (test.__name__, exc))
    if failures:
        print('\n%d failure(s)' % len(failures))
        sys.exit(1)
    print('\nAll action combat self-tests passed.')
