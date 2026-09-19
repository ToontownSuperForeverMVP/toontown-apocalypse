"""Procedural street contracts.

A run gets a handful of disposable objectives chosen from templates.  They do
not tell a story; they just give the player a reason to keep fighting and a
bonus payout.  The generator and the :class:`Objective` value object are pure
Python so the AI can own them and the client can render them from the wire
representation ``(kind, param, target, progress, beans)``.
"""

import random

from toontown.action import ActionGlobals


class Objective:
    __slots__ = ('kind', 'param', 'target', 'progress', 'beans', 'complete')

    def __init__(self, kind, param, target, beans, progress=0, complete=False):
        self.kind = int(kind)
        self.param = int(param)
        self.target = int(target)
        self.progress = int(progress)
        self.beans = int(beans)
        self.complete = bool(complete)

    def toWire(self):
        return [self.kind, self.param, self.target, self.progress, self.beans]

    @classmethod
    def fromWire(cls, data):
        kind, param, target, progress, beans = data
        return cls(kind, param, target, beans, progress=progress, complete=progress >= target)

    def addProgress(self, amount=1):
        """Advance the objective.  Returns True when this call completed it."""
        if self.complete:
            return False
        self.progress = min(self.target, self.progress + int(amount))
        if self.progress >= self.target:
            self.complete = True
            return True
        return False

    def setProgress(self, value):
        if self.complete:
            return False
        self.progress = max(self.progress, min(self.target, int(value)))
        if self.progress >= self.target:
            self.complete = True
            return True
        return False

    def __repr__(self):
        return 'Objective(kind=%d, param=%d, %d/%d, beans=%d)' % (
            self.kind, self.param, self.progress, self.target, self.beans)


class ObjectiveGenerator:
    """Builds a contract for a street run.

    ``deptWeights`` mirrors the suit planner's department frequency list so
    department objectives only ask for Cogs that actually spawn here.
    """

    def __init__(self, tier, baseLevels, deptWeights, equippedTracks, rng=None):
        self.tier = max(ActionGlobals.MIN_TIER, min(ActionGlobals.MAX_TIER, int(tier)))
        self.baseLevels = tuple(baseLevels) or (1,)
        self.deptWeights = tuple(deptWeights) if deptWeights else (25, 25, 25, 25)
        self.equippedTracks = [track for track in (equippedTracks or ()) if track is not None and track >= 0]
        self.rng = rng or random.Random()

    # -- helpers ---------------------------------------------------------
    def _beans(self, scale):
        base = ActionGlobals.OBJECTIVE_COMPLETE_BEANS_BASE * (1.0 + 0.35 * (self.tier - 1))
        return max(10, int(round(base * scale)))

    def _pickDept(self):
        total = sum(self.deptWeights)
        if total <= 0:
            return self.rng.randrange(len(ActionGlobals.DEPT_CODES))
        roll = self.rng.uniform(0, total)
        running = 0.0
        for index, weight in enumerate(self.deptWeights):
            running += weight
            if roll <= running:
                return index
        return len(self.deptWeights) - 1

    # -- templates -------------------------------------------------------
    def _defeatAny(self):
        target = 8 + 2 * self.tier
        return Objective(ActionGlobals.OBJ_DEFEAT_ANY, 0, target, self._beans(1.0))

    def _defeatDept(self):
        dept = self._pickDept()
        target = 4 + self.tier
        return Objective(ActionGlobals.OBJ_DEFEAT_DEPT, dept, target, self._beans(1.2))

    def _defeatLevel(self):
        level = min(ActionGlobals.MAX_COG_LEVEL, max(self.baseLevels) + max(0, (self.tier - 1) // 3))
        target = 3 + self.tier // 2
        return Objective(ActionGlobals.OBJ_DEFEAT_LEVEL, level, target, self._beans(1.4))

    def _dodge(self):
        target = 5 + self.tier
        return Objective(ActionGlobals.OBJ_DODGE, 0, target, self._beans(0.9))

    def _reachStage(self):
        stage = min(ActionGlobals.PRESSURE_INVASION, ActionGlobals.PRESSURE_ALERT + (self.tier - 1) // 3)
        return Objective(ActionGlobals.OBJ_REACH_STAGE, stage, 1, self._beans(1.6))

    def _defeatElite(self):
        target = 1 + self.tier // 4
        return Objective(ActionGlobals.OBJ_DEFEAT_ELITE, 0, target, self._beans(1.8))

    def _findCache(self):
        return Objective(ActionGlobals.OBJ_FIND_CACHE, 0, 1, self._beans(1.3))

    def _hitsWithTrack(self):
        track = self.rng.choice(self.equippedTracks)
        target = 15 + 5 * self.tier
        return Objective(ActionGlobals.OBJ_HITS_WITH_TRACK, track, target, self._beans(1.0))

    def _killChain(self):
        target = min(6, 3 + self.tier // 2)
        return Objective(ActionGlobals.OBJ_KILL_CHAIN, 0, target, self._beans(1.35))

    def generate(self, count=ActionGlobals.OBJECTIVES_PER_RUN, allowCache=True):
        templates = [self._defeatAny, self._defeatDept, self._defeatLevel, self._dodge]
        if self.tier >= 3:
            templates.append(self._reachStage)
        if self.tier >= 4:
            templates.append(self._defeatElite)
        if self.tier >= 2:
            templates.append(self._killChain)
        if allowCache:
            templates.append(self._findCache)
        damageTracks = [track for track in self.equippedTracks
                        if track not in (ActionGlobals.HEAL_TRACK, ActionGlobals.LURE_TRACK)]
        if damageTracks:
            self.equippedTracks = damageTracks
            templates.append(self._hitsWithTrack)

        objectives = []
        usedKinds = set()
        self.rng.shuffle(templates)
        # The first contract is always a plain defeat objective so a run
        # never opens with three side-conditions.
        first = self._defeatAny()
        objectives.append(first)
        usedKinds.add(first.kind)
        for template in templates:
            if len(objectives) >= count:
                break
            objective = template()
            if objective.kind in usedKinds:
                continue
            usedKinds.add(objective.kind)
            objectives.append(objective)
        return objectives


def contractClearedBeans(tier, profileRewardScale=1.0):
    return int(round(ActionGlobals.CONTRACT_CLEARED_BEANS_BASE * (1.0 + 0.4 * (tier - 1)) * profileRewardScale))


def objectiveXp(objective, tier):
    return int(round(ActionGlobals.OBJECTIVE_COMPLETE_XP_BASE * (1.0 + 0.3 * (tier - 1))))
