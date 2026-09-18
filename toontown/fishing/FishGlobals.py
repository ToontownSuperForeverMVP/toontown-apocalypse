from typing import List, Tuple, Dict
from toontown.toonbase import TTLocalizer
from math import ceil, pow
import random
from toontown.toonbase import ToontownGlobals
import copy

# Fish Genus enum values (from original Toontown)
class FishGenus:
    BalloonFish = 0
    CatFish = 1
    Clownfish = 2
    Frozen_Fish = 3
    Starfish = 4
    Holy_Mackerel = 5
    Dog_Fish = 6
    AmoreEel = 7
    Nurse_Shark = 8
    King_Crab = 9
    Moon_Fish = 10
    Seahorse = 11
    Pool_Shark = 12
    Bear_Acuda = 13
    CutThroatTrout = 14
    Piano_Tuna = 15
    PBJ_Fish = 16
    DevilRay = 17

# Original Toontown constants
NoMovie = 0
EnterMovie = 1
ExitMovie = 2
CastMovie = 3
PullInMovie = 4
CastTimeout = 45.0
Nothing = 0
QuestItem = 1
FishItem = 2
JellybeanItem = 3
BootItem = 4
GagItem = 5
OverTankLimit = 8
FishItemNewEntry = 9
FishItemNewRecord = 10
BingoBoot = (BootItem, 99)
ProbabilityDict = {93: FishItem,
 94: JellybeanItem,
 100: BootItem}
SortedProbabilityCutoffs = list(ProbabilityDict.keys())
SortedProbabilityCutoffs.sort()
Rod2JellybeanDict = {0: 150,
 1: 200,
 2: 250,
 3: 300,
 4: 450}
HealAmount = 1
JellybeanFishingHolidayScoreMultiplier = 2
GlobalRarityDialBase = 4.3
FishingAngleMax = 50.0
OVERALL_VALUE_SCALE = 95
RARITY_VALUE_SCALE = 0.2
WEIGHT_VALUE_SCALE = 0.05 / 16.0
COLLECT_NO_UPDATE = 0
COLLECT_NEW_ENTRY = 1
COLLECT_NEW_RECORD = 2
RodFileDict = {0: 'phase_4/models/props/pole_treebranch-mod',
 1: 'phase_4/models/props/pole_bamboo-mod',
 2: 'phase_4/models/props/pole_wood-mod',
 3: 'phase_4/models/props/pole_steel-mod',
 4: 'phase_4/models/props/pole_gold-mod'}
RodPriceDict = {0: 0,
 1: 400,
 2: 800,
 3: 1200,
 4: 2000}
RodRarityFactor = {0: 1.0 / (GlobalRarityDialBase * 1),
 1: 1.0 / (GlobalRarityDialBase * 0.975),
 2: 1.0 / (GlobalRarityDialBase * 0.95),
 3: 1.0 / (GlobalRarityDialBase * 0.9),
 4: 1.0 / (GlobalRarityDialBase * 0.85)}
MaxRodId = 4

# Rod weight ranges (minWeight, maxWeight)
RodWeightRanges = {
    0: (0, 4),
    1: (0, 8),
    2: (0, 12),
    3: (4, 16),
    4: (8, 20)
}

# Fish weight ranges and rarities: {genus: [(minWeight, maxWeight, rarity), ...]}
FishData = {
    FishGenus.BalloonFish: [(1, 10, 5), (1, 12, 7), (2, 14, 9)],
    FishGenus.CatFish: [(2, 6, 3), (2, 8, 5), (3, 10, 7)],
    FishGenus.Clownfish: [(1, 4, 2), (1, 6, 4), (2, 8, 6)],
    FishGenus.Frozen_Fish: [(4, 12, 6), (5, 14, 8), (6, 16, 10)],
    FishGenus.Starfish: [(1, 3, 1), (1, 5, 3), (2, 7, 5)],
    FishGenus.Holy_Mackerel: [(3, 10, 5), (4, 12, 7), (5, 14, 9)],
    FishGenus.Dog_Fish: [(2, 8, 4), (3, 10, 6), (4, 12, 8)],
    FishGenus.AmoreEel: [(5, 15, 7), (6, 17, 9), (7, 19, 10)],
    FishGenus.Nurse_Shark: [(8, 16, 8), (9, 18, 9), (10, 20, 10)],
    FishGenus.King_Crab: [(6, 14, 7), (7, 16, 8), (8, 18, 10)],
    FishGenus.Moon_Fish: [(4, 10, 6), (5, 12, 7), (6, 14, 9)],
    FishGenus.Seahorse: [(2, 6, 4), (3, 8, 6), (4, 10, 8)],
    FishGenus.Pool_Shark: [(6, 14, 7), (7, 16, 9), (8, 18, 10)],
    FishGenus.Bear_Acuda: [(8, 18, 9), (9, 19, 10), (10, 20, 10)],
    FishGenus.CutThroatTrout: [(4, 12, 6), (5, 14, 8), (6, 16, 9)],
    FishGenus.Piano_Tuna: [(10, 18, 9), (11, 19, 10), (12, 20, 10)],
    FishGenus.PBJ_Fish: [(3, 9, 5), (4, 11, 7), (5, 13, 8)],
    FishGenus.DevilRay: [(7, 15, 8), (8, 17, 9), (9, 19, 10)]
}

# Zone to fish mapping (simplified - all fish available in all ponds for now)
ZoneToPondId = {
    ToontownGlobals.ToontownCentral: 0,
    ToontownGlobals.DonaldsDock: 1,
    ToontownGlobals.DaisyGardens: 2,
    ToontownGlobals.MinniesMelodyland: 3,
    ToontownGlobals.TheBrrrgh: 4,
    ToontownGlobals.DonaldsDreamland: 5
}

FishAudioFileDict = {
    -1: ("Clownfish.ogg", 1, 1.5, 1.0),
    FishGenus.BalloonFish: ("BalloonFish.ogg", 1, 0, 1.23),
    FishGenus.CatFish: ("CatFish.ogg", 1, 0, 1.26),
    FishGenus.Clownfish: ("Clownfish.ogg", 1, 1.5, 1.0),
    FishGenus.Frozen_Fish: ("Frozen_Fish.ogg", 1, 0, 1.0),
    FishGenus.Starfish: ("Starfish.ogg", 0, 0, 1.25),
    FishGenus.Holy_Mackerel: ("Holy_Mackerel.ogg", 1, 0.9, 1.0),
    FishGenus.Dog_Fish: ("Dog_Fish.ogg", 1, 0, 1.25),
    FishGenus.AmoreEel: ("AmoreEel.ogg", 1, 0, 1.0),
    FishGenus.Nurse_Shark: ("Nurse_Shark.ogg", 0, 0, 1.0),
    FishGenus.King_Crab: ("King_Crab.ogg", 0, 0, 1.0),
    FishGenus.Moon_Fish: ("Moon_Fish.ogg", 0, 1.0, 1.0),
    FishGenus.Seahorse: ("Seahorse.ogg", 1, 0, 1.26),
    FishGenus.Pool_Shark: ("Pool_Shark.ogg", 1, 2.0, 1.0),
    FishGenus.Bear_Acuda: ("Bear_Acuda.ogg", 1, 0, 1.0),
    FishGenus.CutThroatTrout: ("CutThroatTrout.ogg", 1, 0, 1.0),
    FishGenus.Piano_Tuna: ("Piano_Tuna.ogg", 0, 0, 1.0),
    FishGenus.PBJ_Fish: ("PBJ_Fish.ogg", 1, 0, 1.25),
    FishGenus.DevilRay: ("DevilRay.ogg", 0, 0, 1.0),
}

FishFileDict = {
    -1: (4, "clownFish-zero", "clownFish-swim", "clownFish-swim", None, (0.12, 0, -0.15), 0.38, -35, 20),
    FishGenus.BalloonFish:    (4, "balloonFish-zero", "balloonFish-swim", "balloonFish-swim", None, (0.0, 0, 0.0), 1.0, 0, 0),
    FishGenus.CatFish:        (4, "catFish-zero", "catFish-swim", "catFish-swim", None, (1.2, -2.0, 0.5), 0.22, -35, 10),
    FishGenus.Clownfish:      (4, "clownFish-zero", "clownFish-swim", "clownFish-swim", None, (0.12, 0, -0.15), 0.38, -35, 20),
    FishGenus.Frozen_Fish:    (4, "frozenFish-zero", "frozenFish-swim", "frozenFish-swim", None, (0, 0, 0), 0.5, -35, 20),
    FishGenus.Starfish:       (4, "starFish-zero", "starFish-swim", "starFish-swimLOOP", None, (0, 0, -0.38), 0.36, -35, 20),
    FishGenus.Holy_Mackerel:  (4, "holeyMackerel-zero", "holeyMackerel-swim", "holeyMackerel-swim", None, None, 0.4, 0, 0),
    FishGenus.Dog_Fish:       (4, "dogFish-zero", "dogFish-swim", "dogFish-swim", None, (0.8, -1.0, 0.275), 0.33, -38, 10),
    FishGenus.AmoreEel:       (4, "amoreEel-zero", "amoreEel-swim", "amoreEel-swim", None, (0.425, 0, 1.15), 0.5, 0, 60),
    FishGenus.Nurse_Shark:    (4, "nurseShark-zero", "nurseShark-swim", "nurseShark-swim", None, (0, 0, -0.15), 0.3, -40, 10),
    FishGenus.King_Crab:      (4, "kingCrab-zero", "kingCrab-swim", "kingCrab-swimLOOP", None, None, 0.4, 0, 0),
    FishGenus.Moon_Fish:      (4, "moonFish-zero", "moonFish-swim", "moonFish-swimLOOP", None, (-1.2, 14, -2.0), 0.33, 0, -10),
    FishGenus.Seahorse:       (4, "seaHorse-zero", "seaHorse-swim", "seaHorse-swim", None, (-0.57, 0.0, -2.1), 0.23, 33, -10),
    FishGenus.Pool_Shark:     (4, "poolShark-zero", "poolShark-swim", "poolShark-swim", None, (-0.45, 0, -1.8), 0.33, 45, 0),
    FishGenus.Bear_Acuda:     (4, "BearAcuda-zero", "BearAcuda-swim", "BearAcuda-swim", None, (0.65, 0, -3.3), 0.2, -35, 20),
    FishGenus.CutThroatTrout: (4, "cutThroatTrout-zero", "cutThroatTrout-swim", "cutThroatTrout-swim", None, (-0.2, 0, -0.1), 0.5, 35, 20),
    FishGenus.Piano_Tuna:     (4, "pianoTuna-zero", "pianoTuna-swim", "pianoTuna-swim", None, (0.3, 0, 0.0), 0.6, 40, 30),
    FishGenus.PBJ_Fish:       (4, "PBJfish-zero", "PBJfish-swim", "PBJfish-swim", None, (0, 0, 0.72), 0.31, -35, 10),
    FishGenus.DevilRay:       (4, "devilRay-zero", "devilRay-swim", "devilRay-swim", None, (0, 0, 0), 0.4, -35, 20),
}

TrophyDict = {0: (TTLocalizer.FishTrophyNameDict[0],),
 1: (TTLocalizer.FishTrophyNameDict[1],),
 2: (TTLocalizer.FishTrophyNameDict[2],),
 3: (TTLocalizer.FishTrophyNameDict[3],),
 4: (TTLocalizer.FishTrophyNameDict[4],),
 5: (TTLocalizer.FishTrophyNameDict[5],),
 6: (TTLocalizer.FishTrophyNameDict[6],)}
TTG = ToontownGlobals


def getSpecies(genus):
    """Get all species for a genus"""
    return list(range(len(FishData.get(genus, []))))


def getGenera():
    """Get all fish genera"""
    return list(FishData.keys())


def getNumRods():
    return len(RodPriceDict)


def getCastCost(rodId):
    # Original Toontown didn't have cast cost, return 0
    return 0


def canBeCaughtByRod(genus, species, rodIndex):
    minFishWeight, maxFishWeight = getWeightRange(genus, species)
    minRodWeight, maxRodWeight = getRodWeightRange(rodIndex)
    if minRodWeight <= maxFishWeight and maxRodWeight >= minFishWeight:
        return 1
    else:
        return 0


def getRodWeightRange(rodIndex):
    return RodWeightRanges.get(rodIndex, (0, 20))


def __rollRarityDice(rodId, rNumGen):
    if rNumGen is None:
        diceRoll = random.random()
    else:
        diceRoll = rNumGen.random()
    exp = RodRarityFactor[rodId]
    rarity = int(ceil(10 * (1 - pow(diceRoll, exp))))
    if rarity <= 0:
        rarity = 1
    return rarity


def getRandomWeight(genus, species, rodIndex=None, rNumGen=None):
    minFishWeight, maxFishWeight = getWeightRange(genus, species)
    if rodIndex is None:
        minWeight = minFishWeight
        maxWeight = maxFishWeight
    else:
        minRodWeight, maxRodWeight = getRodWeightRange(rodIndex)
        minWeight = max(minFishWeight, minRodWeight)
        maxWeight = min(maxFishWeight, maxRodWeight)

    randNumA = (rNumGen or random).random()
    randNumB = (rNumGen or random).random()
    randNum = (randNumA + randNumB) / 2.0
    randWeight = minWeight + (maxWeight - minWeight) * randNum
    return int(round(randWeight * 16))


__fish_rarity_cache = {}


def getRandomFishVitals(zoneId, rodId, rNumGen=None, location=None, forceRarity=None):
    # Get all catchable fish for this zone
    catchable_fish = []
    for genus in FishData:
        for species_idx, (minW, maxW, rarity) in enumerate(FishData[genus]):
            if canBeCaughtByRod(genus, species_idx, rodId):
                catchable_fish.append((genus, species_idx, rarity))
    
    rolledRarity = forceRarity or __rollRarityDice(rodId, rNumGen)

    # Filter for matchingrarity
    catchableFishOfRarity = [
        (fishGenus, speciesIndex)
        for fishGenus, speciesIndex, rarity in catchable_fish
        if rarity == rolledRarity
    ]

    # Pick a random fish (if present)
    if catchableFishOfRarity:
        genus, species = (rNumGen or random).choice(catchableFishOfRarity)
        weight = getRandomWeight(genus, species, rodId, rNumGen)
        return 1, genus, species, weight

    return 0, 0, 0, 0


def getWeightRange(genus, species):
    fish_species = FishData.get(genus, [])
    if species < len(fish_species):
        minW, maxW, rarity = fish_species[species]
        return (minW, maxW)
    return (1, 10)


def getRarity(genus, species):
    fish_species = FishData.get(genus, [])
    if species < len(fish_species):
        minW, maxW, rarity = fish_species[species]
        return rarity
    return 5


def getValue(genus, species, weight):
    rarity = getRarity(genus, species)
    rarityValue = pow(RARITY_VALUE_SCALE * rarity, 1.5)
    weightValue = pow(WEIGHT_VALUE_SCALE * weight, 1.1)
    value = OVERALL_VALUE_SCALE * (rarityValue + weightValue)
    finalValue = int(ceil(value))
    base = getBase()
    if hasattr(base, 'cr') and base.cr:
        if hasattr(base.cr, 'newsManager') and base.cr.newsManager:
            holidayIds = base.cr.newsManager.getHolidayIdList()
            if ToontownGlobals.JELLYBEAN_FISHING_HOLIDAY in holidayIds or ToontownGlobals.JELLYBEAN_FISHING_HOLIDAY_MONTH in holidayIds:
                finalValue *= JellybeanFishingHolidayScoreMultiplier
    elif hasattr(simbase, 'air') and hasattr(simbase.air, 'holidayManager'):
        if ToontownGlobals.JELLYBEAN_FISHING_HOLIDAY in simbase.air.holidayManager.currentHolidays or ToontownGlobals.JELLYBEAN_FISHING_HOLIDAY_MONTH in simbase.air.holidayManager.currentHolidays:
            finalValue *= JellybeanFishingHolidayScoreMultiplier
    return finalValue


def getTotalNumFish():
    total = 0
    for genus in FishData:
        total += len(FishData[genus])
    return total


def getAllFish():
    fishies = []
    for genusID, thisGenusSpeciesList in FishData.items():
        for speciesID in range(len(thisGenusSpeciesList)):
            fishies.append((genusID, speciesID))
    return fishies


def getPondGeneraList(pondId):
    # Return all genera for now
    return list(FishData.keys())
