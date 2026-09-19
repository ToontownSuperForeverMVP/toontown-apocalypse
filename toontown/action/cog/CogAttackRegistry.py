"""Real-time definitions for every Cog attack.

The legacy :mod:`SuitBattleGlobals` data still owns *which* attacks a Cog
has and how much they hurt.  This registry adds what the turn-based game never
needed: a shape, ranges, wind-up/recovery timing, projectile speed and how the
attack mutates as difficulty rises.

Shapes
------
MELEE       short range swing; hit if the Toon is inside range and a cone in
            front of the Cog when the wind-up ends.
PROJECTILE  a prop is thrown at the Toon's predicted position; hit if the Toon
            is close to the projectile's line of travel at impact time.
AOE         a pulse around the Cog (Power Trip, Quake ...); hit if inside the
            radius when it fires.  High mutations add a second pulse.
BEAM        an instant cone in the direction the Cog was facing when the
            wind-up started (Glower Power, Buzz Word ...).  Side-step it.

Everything here is plain data so it is shared by the AI (resolution) and the
client (telegraphs, props and sounds).
"""

from dataclasses import dataclass, replace
import random

from toontown.battle.SuitBattleGlobals import SuitAttackType

SHAPE_MELEE = 0
SHAPE_PROJECTILE = 1
SHAPE_AOE = 2
SHAPE_BEAM = 3

SHAPE_NAMES = ('melee', 'projectile', 'aoe', 'beam')

MAX_MUTATION = 4


@dataclass(frozen=True)
class RealtimeCogAttack:
    attack: SuitAttackType
    shape: int
    minRange: float
    maxRange: float
    windup: float
    recovery: float
    cooldown: float
    projectileSpeed: float = 0.0
    hitRadius: float = 2.2
    splashRadius: float = 0.0
    coneHalfAngle: float = 0.0
    tracking: float = 0.0
    damageMult: float = 1.0
    pulses: int = 1
    moveDuringRecovery: bool = False
    prop: str = ''
    sound: str = ''
    telegraphColor: tuple = (1.0, 0.35, 0.2, 0.85)

    @property
    def attackId(self):
        return int(self.attack)

    def travelTime(self, distance):
        if self.shape != SHAPE_PROJECTILE or self.projectileSpeed <= 0.0:
            return 0.0
        return max(0.05, distance / self.projectileSpeed)


def mutate(base, mutation):
    """Return a copy of ``base`` adjusted for the difficulty mutation level.

    Mutations change *behaviour* before they change numbers:

    1. shorter wind-up
    2. better tracking / wider cone / bigger radius
    3. second pulse (AOE) or double-tap (PROJECTILE)
    4. the Cog may move during recovery
    """
    mutation = max(0, min(MAX_MUTATION, int(mutation)))
    if mutation == 0:
        return base
    windup = base.windup * (1.0 - 0.08 * mutation)
    recovery = base.recovery * (1.0 - 0.10 * mutation)
    cooldown = base.cooldown * (1.0 - 0.07 * mutation)
    tracking = base.tracking
    cone = base.coneHalfAngle
    splash = base.splashRadius
    hitRadius = base.hitRadius
    pulses = base.pulses
    moveDuringRecovery = base.moveDuringRecovery
    if mutation >= 2:
        tracking = min(1.0, tracking + 0.25 * (mutation - 1))
        cone = cone * (1.0 + 0.12 * (mutation - 1))
        splash = splash * (1.0 + 0.08 * (mutation - 1))
        hitRadius = hitRadius * (1.0 + 0.05 * (mutation - 1))
    if mutation >= 3 and base.shape in (SHAPE_AOE, SHAPE_PROJECTILE):
        pulses = max(pulses, 2)
    if mutation >= 4:
        moveDuringRecovery = True
    return replace(base, windup=max(0.3, windup), recovery=max(0.2, recovery),
                   cooldown=max(0.8, cooldown), tracking=tracking, coneHalfAngle=cone,
                   splashRadius=splash, hitRadius=hitRadius, pulses=pulses,
                   moveDuringRecovery=moveDuringRecovery)


def _melee(attack, maxRange=5.5, windup=0.6, recovery=0.7, cooldown=2.2, cone=55.0,
           damageMult=1.0, prop='', sound=''):
    return RealtimeCogAttack(attack, SHAPE_MELEE, 0.0, maxRange, windup, recovery, cooldown,
                             coneHalfAngle=cone, damageMult=damageMult, prop=prop, sound=sound,
                             telegraphColor=(1.0, 0.75, 0.2, 0.85))


def _projectile(attack, minRange=4.0, maxRange=30.0, windup=0.7, recovery=0.6, cooldown=2.6,
                speed=26.0, hitRadius=2.2, tracking=0.35, splash=0.0, damageMult=1.0,
                prop='paper', sound=''):
    return RealtimeCogAttack(attack, SHAPE_PROJECTILE, minRange, maxRange, windup, recovery, cooldown,
                             projectileSpeed=speed, hitRadius=hitRadius, splashRadius=splash,
                             tracking=tracking, damageMult=damageMult, prop=prop, sound=sound,
                             telegraphColor=(1.0, 0.4, 0.2, 0.85))


def _aoe(attack, radius=8.0, windup=1.1, recovery=0.9, cooldown=4.5, damageMult=1.0, sound=''):
    return RealtimeCogAttack(attack, SHAPE_AOE, 0.0, radius * 1.4, windup, recovery, cooldown,
                             splashRadius=radius, damageMult=damageMult, sound=sound,
                             telegraphColor=(0.9, 0.2, 0.9, 0.85))


def _beam(attack, minRange=3.0, maxRange=22.0, windup=0.8, recovery=0.6, cooldown=3.0,
          cone=14.0, damageMult=1.0, prop='', sound=''):
    return RealtimeCogAttack(attack, SHAPE_BEAM, minRange, maxRange, windup, recovery, cooldown,
                             coneHalfAngle=cone, damageMult=damageMult, prop=prop, sound=sound,
                             telegraphColor=(0.3, 0.6, 1.0, 0.85))


_A = SuitAttackType

# Explicit tuning.  Anything missing here falls back to the shape defaults in
# _DEFAULT_BY_ANIM below so every attack a Cog owns is playable.
_REGISTRY = {
    # -- Bossbot -----------------------------------------------------------
    _A.POUND_KEY: _melee(_A.POUND_KEY, maxRange=6.0, windup=0.55, cooldown=2.0, prop='phone',
                         sound='SA_audit.ogg'),
    _A.SHRED: _melee(_A.SHRED, maxRange=5.0, windup=0.8, recovery=0.9, cooldown=3.0, damageMult=1.1,
                     prop='shredder', sound='SA_shred.ogg'),
    _A.CLIPON_TIE: _projectile(_A.CLIPON_TIE, maxRange=26.0, windup=0.6, cooldown=2.2, speed=30.0,
                               prop='clip-on-tie'),
    _A.FOUNTAIN_PEN: _beam(_A.FOUNTAIN_PEN, maxRange=18.0, windup=0.7, cone=10.0, prop='pen',
                           sound='SA_fountain_pen.ogg'),
    _A.RUB_OUT: _melee(_A.RUB_OUT, maxRange=7.0, windup=0.75, cooldown=2.8, cone=70.0,
                       sound='SA_rubout.ogg'),
    _A.FINGER_WAG: _beam(_A.FINGER_WAG, maxRange=14.0, windup=0.6, recovery=0.5, cooldown=2.4, cone=18.0,
                         sound='SA_finger_wag.ogg'),
    _A.WRITE_OFF: _projectile(_A.WRITE_OFF, maxRange=28.0, windup=0.9, cooldown=3.2, speed=24.0,
                              tracking=0.5, prop='pen', sound='SA_writeoff_pen_only.ogg'),
    _A.FILL_WITH_LEAD: _melee(_A.FILL_WITH_LEAD, maxRange=6.5, windup=0.7, cooldown=2.6, prop='pencil'),
    _A.RUBBER_STAMP: _melee(_A.RUBBER_STAMP, maxRange=6.0, windup=0.5, recovery=0.6, cooldown=1.9,
                            prop='rubber-stamp', sound='SA_rubber_stamp.ogg'),
    _A.RAZZLE_DAZZLE: _beam(_A.RAZZLE_DAZZLE, maxRange=20.0, windup=0.9, cone=16.0, prop='smile',
                            sound='SA_razzle_dazzle.ogg'),
    _A.SYNERGY: _aoe(_A.SYNERGY, radius=9.0, windup=1.2, cooldown=5.0, sound='SA_synergy.ogg'),
    _A.TEE_OFF: _projectile(_A.TEE_OFF, maxRange=34.0, windup=0.8, cooldown=3.0, speed=36.0,
                            tracking=0.4, prop='golf-ball', sound='SA_tee_off.ogg'),
    _A.BRAIN_STORM: _aoe(_A.BRAIN_STORM, radius=7.0, windup=1.0, cooldown=4.2, sound='SA_brainstorm.ogg'),
    _A.BUZZ_WORD: _beam(_A.BUZZ_WORD, maxRange=20.0, windup=0.7, cone=15.0, sound='SA_buzz_word.ogg'),
    _A.DEMOTION: _beam(_A.DEMOTION, maxRange=18.0, windup=0.9, cone=12.0, damageMult=1.15,
                       sound='SA_demotion.ogg'),
    _A.DOWNSIZE: _aoe(_A.DOWNSIZE, radius=8.0, windup=1.1, cooldown=4.5),
    _A.PINK_SLIP: _projectile(_A.PINK_SLIP, maxRange=28.0, windup=0.7, cooldown=2.5, speed=28.0,
                              prop='pink-slip', sound='SA_pink_slip.ogg'),
    _A.HEAD_SHRINK: _beam(_A.HEAD_SHRINK, maxRange=16.0, windup=0.9, cone=12.0,
                          sound='SA_head_shrink_only.ogg'),
    _A.PARADIGM_SHIFT: _aoe(_A.PARADIGM_SHIFT, radius=9.0, windup=1.25, cooldown=5.5, damageMult=1.1,
                            sound='SA_paradigm_shift.ogg'),
    _A.POWER_TRIP: _aoe(_A.POWER_TRIP, radius=8.0, windup=0.9, recovery=0.9, cooldown=4.0),
    _A.EVIL_EYE: _beam(_A.EVIL_EYE, maxRange=24.0, windup=0.8, cone=10.0, prop='evil-eye',
                       sound='SA_evil_eye.ogg'),
    _A.PLAY_HARDBALL: _projectile(_A.PLAY_HARDBALL, maxRange=32.0, windup=0.7, cooldown=2.6, speed=40.0,
                                  tracking=0.5, prop='baseball', sound='SA_hardball.ogg'),
    _A.CIGAR_SMOKE: _beam(_A.CIGAR_SMOKE, maxRange=12.0, windup=0.8, cone=22.0, prop='cigar'),
    _A.FLOOD_THE_MARKET: _beam(_A.FLOOD_THE_MARKET, maxRange=20.0, windup=1.0, cone=14.0, damageMult=1.1),
    _A.SONG_AND_DANCE: _aoe(_A.SONG_AND_DANCE, radius=9.0, windup=1.3, cooldown=5.0),
    _A.TREMOR: _aoe(_A.TREMOR, radius=10.0, windup=1.2, cooldown=5.0, damageMult=1.15, sound='SA_tremor.ogg'),
    # -- Sellbot -----------------------------------------------------------
    _A.FREEZE_ASSETS: _beam(_A.FREEZE_ASSETS, maxRange=18.0, windup=0.9, cone=12.0),
    _A.HOT_AIR: _beam(_A.HOT_AIR, maxRange=16.0, windup=0.7, cone=18.0, sound='SA_hot_air.ogg'),
    _A.PICK_POCKET: _melee(_A.PICK_POCKET, maxRange=5.5, windup=0.45, recovery=0.5, cooldown=1.8,
                           sound='SA_pick_pocket.ogg'),
    _A.ROLODEX: _projectile(_A.ROLODEX, maxRange=24.0, windup=0.7, cooldown=2.4, speed=26.0,
                            prop='rollodex', sound='SA_rolodex.ogg'),
    _A.SCHMOOZE: _beam(_A.SCHMOOZE, maxRange=18.0, windup=0.8, cone=16.0),
    _A.SPIN: _aoe(_A.SPIN, radius=7.0, windup=1.0, cooldown=4.2),
    _A.WATERCOOLER: _beam(_A.WATERCOOLER, maxRange=14.0, windup=0.9, cone=14.0, prop='watercooler',
                          sound='SA_watercooler_spray_only.ogg'),
    _A.HALF_WINDSOR: _projectile(_A.HALF_WINDSOR, maxRange=26.0, windup=0.65, cooldown=2.3, speed=30.0,
                                 prop='half-windsor'),
    _A.POWER_TIE: _projectile(_A.POWER_TIE, maxRange=30.0, windup=0.7, cooldown=2.6, speed=32.0,
                              tracking=0.45, prop='power-tie', sound='SA_powertie_throw.ogg'),
    _A.MUMBO_JUMBO: _beam(_A.MUMBO_JUMBO, maxRange=18.0, windup=0.8, cone=15.0, sound='SA_mumbo_jumbo.ogg'),
    _A.GLOWER_POWER: _beam(_A.GLOWER_POWER, maxRange=26.0, windup=0.75, cone=9.0, damageMult=1.1,
                           sound='SA_glower_power.ogg'),
    _A.EVICTION_NOTICE: _projectile(_A.EVICTION_NOTICE, maxRange=28.0, windup=0.8, cooldown=2.8, speed=26.0,
                                    prop='paper'),
    _A.RED_TAPE: _projectile(_A.RED_TAPE, maxRange=22.0, windup=0.8, cooldown=3.0, speed=22.0, hitRadius=2.6,
                             prop='redtape', sound='SA_red_tape.ogg'),
    _A.JARGON: _beam(_A.JARGON, maxRange=18.0, windup=0.8, cone=15.0, sound='SA_jargon.ogg'),
    _A.FILIBUSTER: _beam(_A.FILIBUSTER, maxRange=20.0, windup=1.0, cone=13.0, sound='SA_filibuster.ogg'),
    _A.DOUBLE_TALK: _beam(_A.DOUBLE_TALK, maxRange=18.0, windup=0.8, cone=16.0),
    _A.GUILT_TRIP: _aoe(_A.GUILT_TRIP, radius=9.0, windup=1.2, cooldown=5.0, sound='SA_guilt_trip.ogg'),
    _A.RE_ORG: _aoe(_A.RE_ORG, radius=8.0, windup=1.1, cooldown=4.6),
    # -- Cashbot -----------------------------------------------------------
    _A.AUDIT: _beam(_A.AUDIT, maxRange=18.0, windup=0.8, cone=13.0, prop='phone', sound='SA_audit.ogg'),
    _A.CALCULATE: _beam(_A.CALCULATE, maxRange=18.0, windup=0.8, cone=13.0, prop='calculator'),
    _A.TABULATE: _beam(_A.TABULATE, maxRange=18.0, windup=0.8, cone=13.0, prop='calculator'),
    _A.CRUNCH: _projectile(_A.CRUNCH, maxRange=26.0, windup=0.8, cooldown=2.8, speed=28.0, damageMult=1.1,
                           prop='calculator'),
    _A.BOUNCE_CHECK: _projectile(_A.BOUNCE_CHECK, maxRange=28.0, windup=0.7, cooldown=2.5, speed=27.0,
                                 prop='bounced-check'),
    _A.LIQUIDATE: _aoe(_A.LIQUIDATE, radius=8.0, windup=1.1, cooldown=4.6, sound='SA_liquidate.ogg'),
    _A.MARKET_CRASH: _projectile(_A.MARKET_CRASH, maxRange=30.0, windup=1.0, cooldown=3.6, speed=24.0,
                                 hitRadius=2.6, splash=4.0, damageMult=1.2, prop='newspaper'),
    _A.PECKING_ORDER: _projectile(_A.PECKING_ORDER, maxRange=26.0, windup=0.7, cooldown=2.6, speed=30.0,
                                  tracking=0.6, prop='bird'),
    _A.WITHDRAWAL: _beam(_A.WITHDRAWAL, maxRange=16.0, windup=0.9, cone=12.0, sound='SA_withdrawl.ogg'),
    _A.BITE: _melee(_A.BITE, maxRange=6.0, windup=0.5, cooldown=2.0, prop='teeth'),
    _A.CHOMP: _melee(_A.CHOMP, maxRange=6.5, windup=0.6, cooldown=2.3, damageMult=1.1, prop='teeth'),
    _A.SACKED: _projectile(_A.SACKED, maxRange=24.0, windup=0.8, cooldown=2.8, speed=24.0, prop='sandbag'),
    _A.SANDTRAP: _projectile(_A.SANDTRAP, maxRange=30.0, windup=0.8, cooldown=3.0, speed=30.0,
                             prop='golf-ball'),
    _A.FIVE_O_CLOCK_SHADOW: _beam(_A.FIVE_O_CLOCK_SHADOW, maxRange=20.0, windup=0.9, cone=12.0),
    _A.CANNED: _projectile(_A.CANNED, maxRange=26.0, windup=0.8, cooldown=2.8, speed=26.0, prop='can',
                           sound='SA_canned_tossup_only.ogg'),
    _A.HANG_UP: _projectile(_A.HANG_UP, maxRange=24.0, windup=0.7, cooldown=2.6, speed=26.0, prop='receiver',
                            sound='SA_hangup.ogg'),
    # -- Lawbot ------------------------------------------------------------
    _A.RESTRAINING_ORDER: _projectile(_A.RESTRAINING_ORDER, maxRange=28.0, windup=0.8, cooldown=2.8,
                                      speed=26.0, prop='paper'),
    _A.THROW_BOOK: _projectile(_A.THROW_BOOK, maxRange=26.0, windup=0.9, cooldown=3.0, speed=24.0,
                               damageMult=1.15, prop='lawbook'),
    _A.LEGALESE: _beam(_A.LEGALESE, maxRange=18.0, windup=0.8, cone=15.0),
    _A.GAVEL: _melee(_A.GAVEL, maxRange=6.5, windup=0.7, cooldown=2.6, damageMult=1.15, prop='gavel'),
    _A.QUAKE: _aoe(_A.QUAKE, radius=10.0, windup=1.3, cooldown=5.5, damageMult=1.2),
    _A.SHAKE: _aoe(_A.SHAKE, radius=8.0, windup=1.1, cooldown=4.8),
    _A.FIRED: _beam(_A.FIRED, maxRange=16.0, windup=0.9, cone=12.0, damageMult=1.1),
}


# Fallback shapes by the legacy default animation so that attacks without an
# explicit entry still work.  The default of defaults is a mid-range beam.
_ANIM_SHAPE_DEFAULTS = {
    'throw-paper': lambda a: _projectile(a, prop='paper'),
    'throw-object': lambda a: _projectile(a, prop='paper'),
    'magic1': lambda a: _beam(a),
    'magic2': lambda a: _aoe(a),
    'magic3': lambda a: _aoe(a),
    'quick-jump': lambda a: _aoe(a),
    'stomp': lambda a: _aoe(a),
    'song-and-dance': lambda a: _aoe(a),
    'phone': lambda a: _beam(a, prop='phone'),
    'speak': lambda a: _beam(a),
    'glower': lambda a: _beam(a),
    'smile': lambda a: _beam(a),
    'effort': lambda a: _aoe(a),
    'rubber-stamp': lambda a: _melee(a, prop='rubber-stamp'),
    'pencil-sharpener': lambda a: _melee(a, prop='pencil'),
    'hold-eraser': lambda a: _melee(a),
    'finger-wag': lambda a: _beam(a, cone=18.0),
    'pickpocket': lambda a: _melee(a),
    'shredder': lambda a: _melee(a, prop='shredder'),
    'golf-club-swing': lambda a: _projectile(a, prop='golf-ball'),
    'gavel': lambda a: _melee(a, prop='gavel'),
    'cigar-smoke': lambda a: _beam(a, cone=22.0),
    'roll-o-dex': lambda a: _projectile(a, prop='rollodex'),
    'watercooler': lambda a: _beam(a, prop='watercooler'),
    'pen-squirt': lambda a: _beam(a, prop='pen'),
}


def _defaultForAttack(attack):
    try:
        from toontown.battle import SuitBattleGlobals
        animMap = getattr(SuitBattleGlobals, '__SuitAttacksToDefaultAnimation', {})
        anim = animMap.get(attack, 'magic1')
    except Exception:
        anim = 'magic1'
    factory = _ANIM_SHAPE_DEFAULTS.get(anim)
    if factory is None:
        return _beam(attack)
    return factory(attack)


def getRealtimeAttack(attack):
    """Return the RealtimeCogAttack for a SuitAttackType (never None)."""
    if not isinstance(attack, SuitAttackType):
        attack = SuitAttackType(int(attack))
    definition = _REGISTRY.get(attack)
    if definition is None:
        definition = _defaultForAttack(attack)
        _REGISTRY[attack] = definition
    return definition


def getRealtimeAttackById(attackId):
    try:
        return getRealtimeAttack(SuitAttackType(int(attackId)))
    except ValueError:
        return None


def buildAttackSet(suitAttributes, mutation=0):
    """Return [(RealtimeCogAttack, SuitAttackAttribute)] for a Cog."""
    result = []
    for attribute in getattr(suitAttributes, 'attacks', ()):
        try:
            realtime = mutate(getRealtimeAttack(attribute.attack), mutation)
        except Exception:
            continue
        result.append((realtime, attribute))
    return result


def chooseAttack(attackSet, distance, rng=random, allowFallback=True):
    """Weighted pick among attacks whose range covers ``distance``.

    Returns (RealtimeCogAttack, SuitAttackAttribute) or None when nothing
    is usable at this distance.  ``allowFallback`` lets a Cog that is a little
    outside every range still pick its longest attack so it keeps pressure.
    """
    candidates = [(rt, attr) for rt, attr in attackSet if rt.minRange <= distance <= rt.maxRange]
    if not candidates:
        if not allowFallback or not attackSet:
            return None
        longest = max(attackSet, key=lambda pair: pair[0].maxRange)
        if distance <= longest[0].maxRange * 1.15 and distance >= longest[0].minRange:
            return longest
        return None
    total = sum(max(1, attr.weight) for _, attr in candidates)
    roll = rng.uniform(0, total)
    running = 0.0
    for pair in candidates:
        running += max(1, pair[1].weight)
        if roll <= running:
            return pair
    return candidates[-1]


def getLongestRange(attackSet):
    if not attackSet:
        return 0.0
    return max(rt.maxRange for rt, _ in attackSet)
