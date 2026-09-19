"""Gag-track progression helpers shared by the client and the AI.

The persisted Toon fields are reused rather than replaced:

* ``trackAccess[track]``  -> owned tier of the track (0 = undiscovered, 1-3)
* ``experience[track]``   -> mastery XP for the track
* ``equippedTracks``      -> up to three track indices, -1 for an empty slot

All helpers accept any object that exposes the DistributedToon(AI) accessors
so they work on both sides of the wire.
"""

from toontown.action import ActionGlobals


def _trackAccessList(toon):
    getter = getattr(toon, 'getTrackAccess', None)
    access = getter() if getter else getattr(toon, 'trackArray', None)
    if not access:
        return [0] * ActionGlobals.NUM_TRACKS
    return list(access)


def _experienceList(toon):
    experience = getattr(toon, 'experience', None)
    if experience is not None and hasattr(experience, 'experience'):
        values = list(experience.experience)
    else:
        getter = getattr(toon, 'getExperience', None)
        values = list(getter()) if getter else []
    if len(values) < ActionGlobals.NUM_TRACKS:
        values += [0] * (ActionGlobals.NUM_TRACKS - len(values))
    return values


def getTrackTier(toon, track):
    access = _trackAccessList(toon)
    if track < 0 or track >= len(access):
        return 0
    return ActionGlobals.getTrackTierFromAccess(access[track])


def getTrackTiers(toon):
    return [ActionGlobals.getTrackTierFromAccess(value) for value in _trackAccessList(toon)]


def isTrackDiscovered(toon, track):
    return getTrackTier(toon, track) > 0


def getDiscoveredTracks(toon):
    return [track for track in ActionGlobals.ALL_TRACKS if isTrackDiscovered(toon, track)]


def getUndiscoveredTracks(toon):
    return [track for track in ActionGlobals.ALL_TRACKS if not isTrackDiscovered(toon, track)]


def getTrackXp(toon, track):
    values = _experienceList(toon)
    if track < 0 or track >= len(values):
        return 0
    return int(values[track])


def getTrackXpList(toon):
    return [int(value) for value in _experienceList(toon)]


def normalizeLoadout(tracks, toon=None):
    """Return a clean three-slot loadout list.

    Duplicates, unknown tracks and (when a Toon is supplied) undiscovered
    tracks are dropped; the result is padded with -1.
    """
    result = []
    for track in tracks or ():
        try:
            track = int(track)
        except (TypeError, ValueError):
            continue
        if track < 0 or track >= ActionGlobals.NUM_TRACKS or track in result:
            continue
        if toon is not None and not isTrackDiscovered(toon, track):
            continue
        result.append(track)
        if len(result) >= ActionGlobals.MAX_EQUIPPED_TRACKS:
            break
    while len(result) < ActionGlobals.MAX_EQUIPPED_TRACKS:
        result.append(-1)
    return result


def getEquippedTracks(toon):
    """Equipped tracks (no empty slots), validated against discovery."""
    getter = getattr(toon, 'getEquippedTracks', None)
    raw = getter() if getter else getattr(toon, 'equippedTracks', None)
    loadout = normalizeLoadout(raw or (), toon)
    return [track for track in loadout if track >= 0]


def getDefaultLoadout(toon):
    """Loadout used when a Toon has never equipped anything."""
    discovered = getDiscoveredTracks(toon)
    preferred = [track for track in (ActionGlobals.THROW_TRACK, ActionGlobals.SQUIRT_TRACK,
                                     ActionGlobals.SOUND_TRACK, ActionGlobals.DROP_TRACK,
                                     ActionGlobals.HEAL_TRACK, ActionGlobals.LURE_TRACK,
                                     ActionGlobals.TRAP_TRACK) if track in discovered]
    return normalizeLoadout(preferred)


def isTrackEquipped(toon, track):
    return track in getEquippedTracks(toon)


def getMaxLevelForTrack(toon, track):
    """Highest 0-based gag level usable in a track (-1 if undiscovered)."""
    return getTrackTier(toon, track) - 1


def canUseGag(toon, track, level):
    if not isTrackEquipped(toon, track):
        return False
    return 0 <= level <= getMaxLevelForTrack(toon, track)


def getPowerRating(toon):
    maxHp = getattr(toon, 'maxHp', None)
    if maxHp is None:
        getter = getattr(toon, 'getMaxHp', None)
        maxHp = getter() if getter else 15
    return ActionGlobals.getPowerRating(getTrackTiers(toon), getTrackXpList(toon),
                                        getEquippedTracks(toon), maxHp or 0)


def getPurchaseState(toon, track):
    """Return (allowed, reason, cost, xpNeeded) for buying the next tier."""
    tier = getTrackTier(toon, track)
    xp = getTrackXp(toon, track)
    money = getattr(toon, 'money', None)
    if money is None:
        getter = getattr(toon, 'getMoney', None)
        money = getter() if getter else 0
    allowed, reason = ActionGlobals.canPurchaseNextTier(tier, xp, money or 0)
    return allowed, reason, ActionGlobals.getNextTierCost(tier), ActionGlobals.getNextTierXpRequirement(tier)


def getDamageMultiplierPercent(toon):
    getter = getattr(toon, 'getDamageMultiplier', None)
    if getter is None:
        return 100
    try:
        return int(getter())
    except Exception:
        return 100


def getGagDamageForToon(toon, gagDef, lured=False, soaked=False):
    if gagDef is None:
        return 0
    return ActionGlobals.getGagDamage(gagDef, getTrackXp(toon, gagDef.track),
                                      getDamageMultiplierPercent(toon), lured=lured, soaked=soaked)
