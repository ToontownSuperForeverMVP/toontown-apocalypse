"""Rolling street difficulty ("Cog Pressure").

The meter is a plain value object so it can be unit tested without Panda.  The
director feeds it elapsed time and combat events; the HUD reads the stage.

Pressure rises while Toons are on the street and rises faster when they are
winning (kills, dodges, objectives).  It never makes the street impossible on
its own: it only unlocks the higher :mod:`ActionGlobals` stage behaviour, and
leaving the street lets it decay back to CALM.
"""

from toontown.action import ActionGlobals


class PressureMeter:

    def __init__(self, tier=ActionGlobals.DEFAULT_TIER):
        self.tier = tier
        self.value = 0.0
        self.peakValue = 0.0
        self.stage = ActionGlobals.PRESSURE_CALM

    def reset(self):
        self.value = 0.0
        self.peakValue = 0.0
        self.stage = ActionGlobals.PRESSURE_CALM

    def setTier(self, tier):
        self.tier = max(ActionGlobals.MIN_TIER, min(ActionGlobals.MAX_TIER, int(tier)))

    def getStage(self):
        return self.stage

    def getStageName(self):
        return ActionGlobals.PRESSURE_STAGE_NAMES[self.stage]

    def getValue(self):
        return int(self.value)

    def _apply(self, delta):
        self.value = max(0.0, min(float(ActionGlobals.MAX_PRESSURE), self.value + delta))
        self.peakValue = max(self.peakValue, self.value)
        oldStage = self.stage
        self.stage = ActionGlobals.getPressureStage(self.value)
        return self.stage != oldStage

    def tick(self, dt, toonsPresent):
        """Advance by ``dt`` seconds.  Returns True when the stage changed."""
        if dt <= 0.0:
            return False
        if toonsPresent:
            rate = ActionGlobals.PRESSURE_PER_SECOND_BASE + ActionGlobals.PRESSURE_PER_SECOND_PER_TIER * self.tier
            return self._apply(rate * dt)
        return self._apply(-ActionGlobals.PRESSURE_DECAY_PER_SECOND * dt)

    def onCogDefeated(self, cogLevel, elite=False):
        delta = ActionGlobals.PRESSURE_PER_KILL_BASE + ActionGlobals.PRESSURE_PER_KILL_PER_LEVEL * cogLevel
        if elite:
            delta += ActionGlobals.PRESSURE_PER_ELITE_KILL
        return self._apply(delta)

    def onObjectiveComplete(self):
        return self._apply(ActionGlobals.PRESSURE_PER_OBJECTIVE)

    def onDodge(self):
        return self._apply(ActionGlobals.PRESSURE_PER_DODGE)

    def onToonDied(self):
        return self._apply(-(self.value * (1.0 - ActionGlobals.PRESSURE_DEATH_MULTIPLIER)))

    def addRaw(self, delta):
        return self._apply(delta)

    def setValue(self, value):
        self.value = 0.0
        return self._apply(float(value))
