"""Shared constants and pure balance formulas for Toontown Apocalypse.

Everything in this module is deliberately free of ShowBase / AI globals so it
can be imported by the client, the AI server and plain unit tests alike.  All
tuning knobs for the street-combat loop live here:

* difficulty tiers and the player-relative :class:`DifficultyProfile`
* rolling street "Cog Pressure" stages
* the three-tier gag track economy and every gag definition
* reward (jellybean / mastery XP) formulas
* Cog combat roles used by the real-time AI
"""

from dataclasses import dataclass, replace
import math
import random

from toontown.toonbase import ToontownBattleGlobals

# ---------------------------------------------------------------------------
# Tracks
# ---------------------------------------------------------------------------
HEAL_TRACK = ToontownBattleGlobals.HEAL_TRACK
TRAP_TRACK = ToontownBattleGlobals.TRAP_TRACK
LURE_TRACK = ToontownBattleGlobals.LURE_TRACK
SOUND_TRACK = ToontownBattleGlobals.SOUND_TRACK
THROW_TRACK = ToontownBattleGlobals.THROW_TRACK
SQUIRT_TRACK = ToontownBattleGlobals.SQUIRT_TRACK
DROP_TRACK = ToontownBattleGlobals.DROP_TRACK
NUM_TRACKS = ToontownBattleGlobals.NUM_GAG_TRACKS
ALL_TRACKS = tuple(range(NUM_TRACKS))

TRACK_NAMES = ('Toon-Up', 'Trap', 'Lure', 'Sound', 'Throw', 'Squirt', 'Drop')
TRACK_SHORT_NAMES = ('TOON-UP', 'TRAP', 'LURE', 'SOUND', 'THROW', 'SQUIRT', 'DROP')
TRACK_COLORS = ToontownBattleGlobals.TrackColors

# ---------------------------------------------------------------------------
# Difficulty tiers
# ---------------------------------------------------------------------------
MIN_TIER = 1
MAX_TIER = 10
DEFAULT_TIER = 1

# Highest Cog level the director will ever spawn on a street.
MAX_COG_LEVEL = 12
MIN_COG_LEVEL = 1

# ---------------------------------------------------------------------------
# Cog Pressure (rolling street difficulty)
# ---------------------------------------------------------------------------
MAX_PRESSURE = 1000

PRESSURE_CALM = 0
PRESSURE_ACTIVE = 1
PRESSURE_ALERT = 2
PRESSURE_CRACKDOWN = 3
PRESSURE_LOCKDOWN = 4
PRESSURE_INVASION = 5
NUM_PRESSURE_STAGES = 6

PRESSURE_STAGE_NAMES = ('CALM', 'ACTIVE', 'ALERT', 'CRACKDOWN', 'LOCKDOWN', 'INVASION')
PRESSURE_STAGE_THRESHOLDS = (0, 100, 220, 360, 520, 700)
# HUD colours per stage (r, g, b, a)
PRESSURE_STAGE_COLORS = ((0.45, 0.85, 0.55, 1.0),
                         (0.75, 0.90, 0.35, 1.0),
                         (0.98, 0.85, 0.25, 1.0),
                         (1.00, 0.60, 0.20, 1.0),
                         (0.95, 0.30, 0.25, 1.0),
                         (0.80, 0.15, 0.85, 1.0))
PRESSURE_STAGE_REWARD_MULT = (1.0, 1.15, 1.35, 1.6, 1.9, 2.3)
PRESSURE_STAGE_LEVEL_OFFSET = (0, 0, 1, 1, 2, 3)

# Pressure gain / decay rates.
PRESSURE_PER_SECOND_BASE = 0.9
PRESSURE_PER_SECOND_PER_TIER = 0.1
PRESSURE_PER_KILL_BASE = 6.0
PRESSURE_PER_KILL_PER_LEVEL = 1.0
PRESSURE_PER_ELITE_KILL = 10.0
PRESSURE_PER_OBJECTIVE = 40.0
PRESSURE_PER_DODGE = 1.5
PRESSURE_DECAY_PER_SECOND = 15.0
PRESSURE_DEATH_MULTIPLIER = 0.5


def getPressureStage(pressure):
    """Return the stage index (0-5) for a raw pressure value."""
    stage = 0
    for index, threshold in enumerate(PRESSURE_STAGE_THRESHOLDS):
        if pressure >= threshold:
            stage = index
    return stage


def getPressureStageFraction(pressure):
    """Return (stage, fraction-within-stage) for HUD bars."""
    stage = getPressureStage(pressure)
    start = PRESSURE_STAGE_THRESHOLDS[stage]
    if stage + 1 < NUM_PRESSURE_STAGES:
        end = PRESSURE_STAGE_THRESHOLDS[stage + 1]
    else:
        end = MAX_PRESSURE
    span = max(1.0, float(end - start))
    return stage, max(0.0, min(1.0, (pressure - start) / span))


# ---------------------------------------------------------------------------
# Real-time Cog states / statuses (shared with the client for presentation)
# ---------------------------------------------------------------------------
COG_PATROL = 0
COG_ALERT = 1
COG_ENGAGE = 2
COG_STAGGER = 3
COG_LURED = 4
COG_DEFEATED = 5
COG_DEPARTING = 6

STATUS_NONE = 0
STATUS_LURED = 1
STATUS_SOAKED = 2
STATUS_STAGGER = 3

# Seconds between a Cog running out of HP and the AI deleting it.  The client
# plays the lose animation and explosion inside this window.
COG_DEATH_DELAY = 2.6
# Seconds the "noticed you" telegraph lasts before a Cog starts hunting.
COG_ALERT_DURATION = 0.7
# How far a Cog will path-find toward a Toon before dropping to direct steering.
COG_NAV_DIRECT_DISTANCE = 20.0
# A Cog that loses its target for this long flies away.
COG_LOST_TARGET_TIMEOUT = 8.0
# Damage relative to max HP that staggers a Cog.
COG_STAGGER_FRACTION = 0.22
COG_STAGGER_DURATION = 0.8
# Minimum delay between attack *starts* against a single Toon (all Cogs).
TOON_INCOMING_ATTACK_GAP = 0.45
# Cog movement update rate to clients while engaged (seconds).
COG_POSITION_SEND_INTERVAL = 0.1

# Hit resolution radii / fudge for the dodge model.  The Toon's position is
# only known to the AI with a little lag so these are deliberately forgiving.
TOON_HIT_RADIUS = 1.6

# ---------------------------------------------------------------------------
# Gag tracks: discovery, tiers, mastery and the loadout
# ---------------------------------------------------------------------------
MAX_TRACK_TIER = 3
MAX_EQUIPPED_TRACKS = 3
# Mastery XP required (cumulative) before tier II / III can be bought.
TRACK_TIER_XP = (0, 500, 1400)
# Jellybean cost of tier II / III.
TRACK_TIER_COST = (0, 1250, 4000)
# Mastery keeps accumulating after tier III for a damage bonus.
MAX_TRACK_XP = 5000
MASTERY_DAMAGE_BONUS = 0.25
# Fresh Toons are handed these on their first street run.
STARTER_TRACKS = (THROW_TRACK, SQUIRT_TRACK)
# The looter-shooter economy needs a much larger pocket than 40 beans.
MAX_JELLYBEANS = 9999

# Power rating contributions.
TRACK_TIER_POWER = (0.0, 10.0, 24.0, 42.0)
TRACK_MASTERY_POWER = 8.0
UNEQUIPPED_TRACK_POWER_FRACTION = 0.35
LAFF_POWER_FRACTION = 0.35
MIN_POWER = 25.0
MAX_POWER = 268.0

# Gag delivery styles (how the client fires and how the AI validates).
GAG_STYLE_PROJECTILE = 0   # arcing prop at the crosshair target
GAG_STYLE_STREAM = 1       # hitscan stream / blast
GAG_STYLE_RADIAL = 2       # every Cog inside a cone / radius around the Toon
GAG_STYLE_DROP = 3         # falls from the sky onto the aim point
GAG_STYLE_LURE = 4         # status effect, no damage
GAG_STYLE_TRAP = 5         # deployable hazard placed on the ground
GAG_STYLE_HEAL = 6         # self toon-up


@dataclass(frozen=True)
class GagDef:
    track: int
    tier: int
    name: str
    style: int
    damage: int
    cooldown: float
    range: float
    splash: float = 0.0
    duration: float = 0.0
    prop: str = ''
    sound: str = ''
    hitSound: str = ''
    heal: int = 0
    cone: float = 0.0
    dropDelay: float = 0.0
    projectileSpeed: float = 0.0
    knockback: float = 0.0

    @property
    def level(self):
        """0-based level inside the track, as sent on the wire."""
        return self.tier - 1


# Indexed as GAG_DEFS[track][tier - 1].
GAG_DEFS = {
    HEAL_TRACK: (
        GagDef(HEAL_TRACK, 1, 'Feather', GAG_STYLE_HEAL, 0, 6.0, 0.0, heal=8,
               prop='feather', sound='AA_heal_tickle.ogg'),
        GagDef(HEAL_TRACK, 2, 'Megaphone', GAG_STYLE_HEAL, 0, 8.0, 0.0, heal=16,
               prop='megaphone', sound='AA_heal_telljoke.ogg'),
        GagDef(HEAL_TRACK, 3, 'Lipstick', GAG_STYLE_HEAL, 0, 12.0, 0.0, heal=30,
               prop='lipstick', sound='AA_heal_smooch.ogg'),
    ),
    TRAP_TRACK: (
        GagDef(TRAP_TRACK, 1, 'Banana Peel', GAG_STYLE_TRAP, 20, 4.0, 20.0, splash=2.5,
               duration=40.0, prop='banana', sound='TL_banana.ogg'),
        GagDef(TRAP_TRACK, 2, 'Trapdoor', GAG_STYLE_TRAP, 40, 6.0, 20.0, splash=3.0,
               duration=45.0, prop='trapdoor', sound='TL_trap_door.ogg'),
        GagDef(TRAP_TRACK, 3, 'TNT', GAG_STYLE_TRAP, 75, 9.0, 20.0, splash=6.0,
               duration=50.0, prop='tnt', sound='TL_dynamite.ogg'),
    ),
    LURE_TRACK: (
        GagDef(LURE_TRACK, 1, '$1 Bill', GAG_STYLE_LURE, 0, 3.0, 30.0, duration=4.0,
               prop='1dollar', sound='TL_small_magnet.ogg'),
        GagDef(LURE_TRACK, 2, 'Big Magnet', GAG_STYLE_LURE, 0, 4.5, 30.0, splash=6.0, duration=6.0,
               prop='big-magnet', sound='TL_large_magnet.ogg'),
        GagDef(LURE_TRACK, 3, 'Hypno-goggles', GAG_STYLE_LURE, 0, 6.0, 30.0, splash=10.0, duration=8.0,
               prop='hypno-goggles', sound='TL_hypnotize.ogg'),
    ),
    SOUND_TRACK: (
        GagDef(SOUND_TRACK, 1, 'Bike Horn', GAG_STYLE_RADIAL, 6, 1.2, 12.0, cone=35.0,
               prop='bikehorn', sound='AA_sound_bikehorn.ogg'),
        GagDef(SOUND_TRACK, 2, 'Aoogah', GAG_STYLE_RADIAL, 12, 1.8, 14.0, cone=180.0,
               prop='aoogah', sound='AA_sound_aoogah.ogg'),
        GagDef(SOUND_TRACK, 3, 'Fog Horn', GAG_STYLE_RADIAL, 22, 2.6, 24.0, cone=25.0, knockback=6.0,
               prop='fog_horn', sound='AA_sound_Fog_Horn.ogg'),
    ),
    THROW_TRACK: (
        GagDef(THROW_TRACK, 1, 'Cupcake', GAG_STYLE_PROJECTILE, 8, 0.55, 45.0,
               prop='cupcake', sound='AA_pie_throw_only.ogg', hitSound='AA_tart_only.ogg',
               projectileSpeed=60.0),
        GagDef(THROW_TRACK, 2, 'Fruit Pie', GAG_STYLE_PROJECTILE, 16, 0.9, 45.0,
               prop='fruitpie', sound='AA_pie_throw_only.ogg', hitSound='AA_wholepie_only.ogg',
               projectileSpeed=52.0, knockback=2.0),
        GagDef(THROW_TRACK, 3, 'Birthday Cake', GAG_STYLE_PROJECTILE, 30, 1.4, 40.0, splash=3.0,
               prop='birthday-cake', sound='AA_pie_throw_only.ogg', hitSound='AA_throw_wedding_cake.ogg',
               projectileSpeed=40.0, knockback=4.0),
    ),
    SQUIRT_TRACK: (
        GagDef(SQUIRT_TRACK, 1, 'Squirting Flower', GAG_STYLE_STREAM, 5, 0.3, 22.0,
               duration=6.0, prop='squirting-flower', sound='AA_squirt_flowersquirt.ogg'),
        GagDef(SQUIRT_TRACK, 2, 'Seltzer Bottle', GAG_STYLE_STREAM, 11, 0.75, 26.0,
               duration=7.0, prop='bottle', sound='AA_squirt_seltzer.ogg', knockback=2.5),
        GagDef(SQUIRT_TRACK, 3, 'Fire Hose', GAG_STYLE_STREAM, 20, 1.2, 30.0, splash=2.0,
               duration=8.0, prop='firehose', sound='AA_squirt_firehose.ogg', knockback=5.0),
    ),
    DROP_TRACK: (
        GagDef(DROP_TRACK, 1, 'Flower Pot', GAG_STYLE_DROP, 14, 1.6, 30.0, splash=3.0,
               dropDelay=0.6, prop='flowerpot', sound='AA_drop_flowerpot.ogg'),
        GagDef(DROP_TRACK, 2, 'Anvil', GAG_STYLE_DROP, 26, 2.4, 30.0, splash=4.5,
               dropDelay=0.8, prop='anvil', sound='AA_drop_anvil.ogg'),
        GagDef(DROP_TRACK, 3, 'Grand Piano', GAG_STYLE_DROP, 48, 3.6, 30.0, splash=6.0,
               dropDelay=1.0, prop='piano', sound='AA_drop_piano.ogg'),
    ),
}

# Status durations / multipliers driven by gags.
SOAKED_DURATION = 6.0
SOAKED_DAMAGE_BONUS = 1.3        # Drop / Sound vs. soaked Cogs
LURED_DAMAGE_BONUS = 1.5         # Throw / Drop vs. lured Cogs
TRAP_STAGGER_DURATION = 1.4
# Extra slack (seconds) the AI accepts for hits that belong to the same volley
# (a cake splash or a fog horn hitting several Cogs at once).
GAG_VOLLEY_WINDOW = 0.35
# Distance slack added to a gag's range when the AI validates a hit.
GAG_RANGE_SLACK = 6.0


def getGagDef(track, level):
    """Return the GagDef for a 0-based level inside a track (None if invalid)."""
    defs = GAG_DEFS.get(track)
    if defs is None or level < 0 or level >= len(defs):
        return None
    return defs[level]


def getTrackTierFromAccess(accessLevel):
    """Map the persisted trackAccess value onto a 0-3 tier.

    Legacy magic words write large values (8) into trackAccess; anything
    above the tier cap simply means "maxed".
    """
    try:
        accessLevel = int(accessLevel)
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_TRACK_TIER, accessLevel))


def getMasteryFraction(xp):
    return max(0.0, min(1.0, float(xp) / float(MAX_TRACK_XP)))


def getNextTierXpRequirement(tier):
    """XP needed before the tier after ``tier`` may be purchased (None at max)."""
    if tier >= MAX_TRACK_TIER:
        return None
    return TRACK_TIER_XP[tier]


def getNextTierCost(tier):
    if tier >= MAX_TRACK_TIER:
        return None
    return TRACK_TIER_COST[tier]


def canPurchaseNextTier(tier, xp, money):
    """Return (allowed, reason) for buying the next tier of a discovered track."""
    if tier <= 0:
        return False, 'undiscovered'
    if tier >= MAX_TRACK_TIER:
        return False, 'maxed'
    if xp < TRACK_TIER_XP[tier]:
        return False, 'xp'
    if money < TRACK_TIER_COST[tier]:
        return False, 'beans'
    return True, ''


def getGagDamage(gagDef, xp, damageMultiplierPercent=100, lured=False, soaked=False):
    """Authoritative damage for a gag hit.

    ``damageMultiplierPercent`` is the fork's per-Toon percentage multiplier
    (100 = normal).  Status bonuses follow the synergy table in the design.
    """
    if gagDef is None or gagDef.damage <= 0:
        return 0
    damage = gagDef.damage * (1.0 + MASTERY_DAMAGE_BONUS * getMasteryFraction(xp))
    damage *= max(10, damageMultiplierPercent) / 100.0
    if lured and gagDef.track in (THROW_TRACK, DROP_TRACK):
        damage *= LURED_DAMAGE_BONUS
    if soaked and gagDef.track in (DROP_TRACK, SOUND_TRACK):
        damage *= SOAKED_DAMAGE_BONUS
    return max(1, int(round(damage)))


def getPowerRating(trackTiers, trackXp, equippedTracks, maxHp):
    """Toon Power Rating used to make tier difficulty player-relative."""
    power = 0.0
    equipped = set(track for track in equippedTracks if track is not None and track >= 0)
    for track in ALL_TRACKS:
        tier = trackTiers[track] if track < len(trackTiers) else 0
        if tier <= 0:
            continue
        xp = trackXp[track] if track < len(trackXp) else 0
        base = TRACK_TIER_POWER[min(tier, MAX_TRACK_TIER)] + TRACK_MASTERY_POWER * getMasteryFraction(xp)
        if track in equipped:
            power += base
        else:
            power += base * UNEQUIPPED_TRACK_POWER_FRACTION
    power += max(0, maxHp) * LAFF_POWER_FRACTION
    return power


def getRelativeStrength(power):
    return max(0.0, min(1.0, (power - MIN_POWER) / (MAX_POWER - MIN_POWER)))


# ---------------------------------------------------------------------------
# Difficulty profile
# ---------------------------------------------------------------------------
@dataclass
class DifficultyProfile:
    tier: int = DEFAULT_TIER
    stage: int = PRESSURE_CALM
    playerPower: float = MIN_POWER
    relativeStrength: float = 0.0
    levelOffset: int = 0          # added to the street's base Cog levels
    damageScale: float = 1.0      # Cog attack damage multiplier
    aggression: float = 1.0       # movement speed / attack cadence multiplier
    attackMutation: int = 0       # 0-4, see CogAttackRegistry.mutate
    populationBonus: int = 0      # added to the street's minimum population
    engageLimit: int = 2          # Cogs allowed to hunt one Toon at once
    spawnInterval: float = 14.0   # seconds between director spawns
    eliteChance: float = 0.02     # chance a spawn becomes an executive
    rewardScale: float = 1.0      # beans multiplier
    xpScale: float = 1.0          # mastery XP multiplier
    detectRange: float = 26.0     # Cog sight range


def getDifficultyProfile(tier, playerPower, pressure):
    """Combine the intentional tier, the Toon's power and street pressure.

    The split is roughly 60% player-relative / 40% absolute so a strong Toon
    still flattens low-tier streets while Tier 10 stays honest for a fresh
    Toon armed with a cupcake and misplaced confidence.
    """
    tier = max(MIN_TIER, min(MAX_TIER, int(tier)))
    stage = getPressureStage(pressure)
    rel = getRelativeStrength(playerPower)
    t = (tier - MIN_TIER) / float(MAX_TIER - MIN_TIER)

    levelOffset = int(round(t * (2.0 + 6.0 * rel))) + PRESSURE_STAGE_LEVEL_OFFSET[stage]
    damageScale = 1.0 + 0.5 * t + 0.3 * t * rel + 0.12 * stage
    aggression = 1.0 + 0.6 * t + 0.1 * stage
    attackMutation = min(4, int(t * 3.2) + (1 if stage >= PRESSURE_CRACKDOWN else 0)
                         + (1 if stage >= PRESSURE_INVASION else 0))
    populationBonus = int(round(t * 6.0)) + stage * 2
    engageLimit = 2 + int(t * 3.0) + (1 if stage >= PRESSURE_ALERT else 0) + (1 if stage >= PRESSURE_LOCKDOWN else 0)
    spawnInterval = max(3.0, 14.0 - 8.0 * t - 1.5 * stage)
    eliteChance = min(0.6, 0.02 + 0.12 * t + 0.05 * stage)
    rewardScale = (1.0 + 0.45 * t) * PRESSURE_STAGE_REWARD_MULT[stage]
    detectRange = 24.0 + 2.0 * tier + 3.0 * stage

    return DifficultyProfile(tier=tier, stage=stage, playerPower=playerPower,
                             relativeStrength=rel, levelOffset=levelOffset,
                             damageScale=damageScale, aggression=aggression,
                             attackMutation=attackMutation, populationBonus=populationBonus,
                             engageLimit=engageLimit, spawnInterval=spawnInterval,
                             eliteChance=eliteChance, rewardScale=rewardScale,
                             xpScale=rewardScale, detectRange=detectRange)


def previewTier(tier, playerPower):
    """Cheap profile for the tier-select panel (pressure assumed CALM)."""
    return getDifficultyProfile(tier, playerPower, 0)


def pickSpawnLevel(profile, baseLevels, rng=random):
    """Choose an actual Cog level for a director spawn."""
    baseLevels = tuple(baseLevels) or (1,)
    level = rng.choice(baseLevels) + profile.levelOffset
    return max(MIN_COG_LEVEL, min(MAX_COG_LEVEL, level))


ELITE_LEVEL_BONUS = 3
ELITE_REWARD_MULT = 1.5

# ---------------------------------------------------------------------------
# Rewards
# ---------------------------------------------------------------------------
KILL_BEANS_BASE = 6
KILL_BEANS_PER_LEVEL = 4
HIT_XP_PER_DAMAGE = 0.5
KILL_XP_BASE = 4
KILL_XP_PER_LEVEL = 2
TRIVIAL_TARGET_XP_FRACTION = 0.25


def isTrivialTarget(cogLevel, playerPower):
    """Farming level-1 Flunkies with a maxed Toon is not mastery."""
    cogPower = cogLevel * 8.0 + 8.0
    return cogPower < playerPower * 0.35


def getKillBeans(cogLevel, profile, elite=False):
    beans = (KILL_BEANS_BASE + KILL_BEANS_PER_LEVEL * cogLevel) * profile.rewardScale
    if elite:
        beans *= ELITE_REWARD_MULT
    return max(1, int(round(beans)))


def getHitXp(damage, cogLevel, profile):
    xp = damage * HIT_XP_PER_DAMAGE * profile.xpScale
    if isTrivialTarget(cogLevel, profile.playerPower):
        xp *= TRIVIAL_TARGET_XP_FRACTION
    return max(1, int(round(xp)))


def getKillXp(cogLevel, profile, elite=False):
    xp = (KILL_XP_BASE + KILL_XP_PER_LEVEL * cogLevel) * profile.xpScale
    if elite:
        xp *= ELITE_REWARD_MULT
    if isTrivialTarget(cogLevel, profile.playerPower):
        xp *= TRIVIAL_TARGET_XP_FRACTION
    return max(1, int(round(xp)))


# ---------------------------------------------------------------------------
# Cog combat roles
# ---------------------------------------------------------------------------
ROLE_PURSUER = 0      # basic chaser, short range
ROLE_SUPPRESSOR = 1   # mid-range, keeps distance
ROLE_BRUISER = 2      # close-range pressure, heavier hits
ROLE_HARASSER = 3     # long-range, mobile, strafes a lot
ROLE_HEAVY = 4        # slow tank, area denial

ROLE_NAMES = ('Pursuer', 'Suppressor', 'Bruiser', 'Harasser', 'Heavy')


@dataclass(frozen=True)
class RoleTuning:
    preferredRange: float
    speed: float
    strafe: float          # 0-1 chance to be strafing while in range
    keepDistance: bool
    staggerResist: float = 1.0


ROLE_TUNING = {
    ROLE_PURSUER: RoleTuning(preferredRange=5.0, speed=7.5, strafe=0.2, keepDistance=False),
    ROLE_SUPPRESSOR: RoleTuning(preferredRange=16.0, speed=6.0, strafe=0.6, keepDistance=True),
    ROLE_BRUISER: RoleTuning(preferredRange=6.0, speed=6.5, strafe=0.3, keepDistance=False, staggerResist=1.3),
    ROLE_HARASSER: RoleTuning(preferredRange=24.0, speed=8.0, strafe=0.9, keepDistance=True),
    ROLE_HEAVY: RoleTuning(preferredRange=9.0, speed=5.0, strafe=0.1, keepDistance=False, staggerResist=2.0),
}

# Default role by Cog tier (0 = Flunky-tier ... 7 = Big Cheese-tier).
ROLE_BY_TIER = (ROLE_PURSUER, ROLE_SUPPRESSOR, ROLE_BRUISER, ROLE_HARASSER,
                ROLE_BRUISER, ROLE_HARASSER, ROLE_HEAVY, ROLE_HEAVY)

# Hand-tuned exceptions keyed by suit DNA name.
ROLE_OVERRIDES = {
    'mm': ROLE_SUPPRESSOR,   # Micromanager
    'dt': ROLE_SUPPRESSOR,   # Double Talker
    'ms': ROLE_HEAVY,        # Mover & Shaker (area denial)
    'le': ROLE_HARASSER,     # Legal Eagle (mobile ranged)
    'gh': ROLE_BRUISER,      # Glad Hander
    'nd': ROLE_HARASSER,     # Name Dropper
    'tf': ROLE_HARASSER,     # Two-Face
    'bs': ROLE_BRUISER,      # Back Stabber
}


def getCogRole(suitName, suitTier):
    role = ROLE_OVERRIDES.get(suitName)
    if role is None:
        role = ROLE_BY_TIER[max(0, min(len(ROLE_BY_TIER) - 1, int(suitTier)))]
    return role


def getCogSpeed(role, profile):
    tuning = ROLE_TUNING[role]
    return tuning.speed * min(1.6, 0.75 + 0.35 * profile.aggression)


# ---------------------------------------------------------------------------
# Procedural objectives
# ---------------------------------------------------------------------------
OBJ_DEFEAT_ANY = 0
OBJ_DEFEAT_DEPT = 1
OBJ_DEFEAT_LEVEL = 2
OBJ_DODGE = 3
OBJ_REACH_STAGE = 4
OBJ_DEFEAT_ELITE = 5
OBJ_FIND_CACHE = 6
OBJ_HITS_WITH_TRACK = 7
OBJ_KILL_CHAIN = 8
NUM_OBJECTIVE_KINDS = 9

OBJECTIVES_PER_RUN = 3
OBJECTIVE_COMPLETE_XP_BASE = 60
OBJECTIVE_COMPLETE_BEANS_BASE = 120
CONTRACT_CLEARED_BEANS_BASE = 300

# Departments in SuitDNA order: c (Bossbot), l (Lawbot), m (Cashbot), s (Sellbot)
DEPT_CODES = ('c', 'l', 'm', 's')
DEPT_NAMES = ('Bossbot', 'Lawbot', 'Cashbot', 'Sellbot')

# ---------------------------------------------------------------------------
# Gag track caches (discovery pickups)
# ---------------------------------------------------------------------------
CACHE_SPAWN_CHANCE_BASE = 0.35
CACHE_SPAWN_CHANCE_PER_TIER = 0.05
CACHE_RESPAWN_KILLS = 20
CACHE_RESPAWN_CHANCE = 0.4
CACHE_CONSOLATION_BEANS = 150
CACHE_GRAB_RADIUS = 2.4

# HUD / announcement kinds (planner.actionAnnounce)
ANNOUNCE_RUN_STARTED = 0
ANNOUNCE_CONTRACT_CLEARED = 1
ANNOUNCE_TOON_DOWN = 2
ANNOUNCE_CACHE_SPAWNED = 3
ANNOUNCE_ELITE_SPAWNED = 4
ANNOUNCE_KILL_CHAIN = 5
ANNOUNCE_BREAKTHROUGH = 6

# Consecutive defeats reward staying in the fight without taking damage.
KILL_CHAIN_WINDOW = 8.0
KILL_CHAIN_BONUS_STEP = 0.05
KILL_CHAIN_MAX_BONUS = 0.25

# A communal reward valve: sustained street clears briefly lower pressure and
# pay every participating Toon. It keeps long runs exciting without turning
# the pressure curve into a one-way death sentence.
BREAKTHROUGH_KILLS = 12
BREAKTHROUGH_BEANS_BASE = 90
BREAKTHROUGH_PRESSURE_RELIEF = 65.0


def clamp(value, low, high):
    return max(low, min(high, value))


def flatDistance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])
