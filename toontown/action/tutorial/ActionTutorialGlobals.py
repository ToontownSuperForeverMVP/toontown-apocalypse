"""Guided onboarding for the real-time street combat loop.

A brand-new Toon is walked through the core loop once on a private street:
move, shoot, build a loadout, dodge a telegraph, and open a Gag Cache.  All of
the *rules* live here as plain data plus :class:`ActionTutorialState`, a pure
step machine that the AI owns and the client mirrors from the wire.  Keeping it
free of Panda globals means it can be unit tested and reasoned about without
booting a client or an AI.

The AI drives the machine from gameplay signals it observes itself (Toon
position, equipped tracks, practice-Cog defeats, cache grabs) or from the
client's ``requestAdvance`` message for the purely explanatory steps.
"""

STEP_WELCOME = 0
STEP_MOVE = 1
STEP_FIRE = 2
STEP_LOADOUT = 3
STEP_DODGE = 4
STEP_CACHE = 5
STEP_FINISH = 6
NUM_STEPS = 7

# Signals the AI feeds into the machine.
SIGNAL_ADVANCE = 'advance'
SIGNAL_MOVE = 'move'
SIGNAL_KILL = 'kill'
SIGNAL_LOADOUT = 'loadout'
SIGNAL_DODGE = 'dodge'
SIGNAL_CACHE = 'cache'

SIGNAL_NAMES = (SIGNAL_ADVANCE, SIGNAL_MOVE, SIGNAL_KILL, SIGNAL_LOADOUT, SIGNAL_DODGE, SIGNAL_CACHE)

# (step, signal that clears it, minimum value of that signal)
_STEP_REQUIREMENTS = (
    (STEP_WELCOME, SIGNAL_ADVANCE, 0),
    (STEP_MOVE, SIGNAL_MOVE, 0),
    (STEP_FIRE, SIGNAL_KILL, 1),
    (STEP_LOADOUT, SIGNAL_LOADOUT, 2),
    (STEP_DODGE, SIGNAL_DODGE, 1),
    (STEP_CACHE, SIGNAL_CACHE, 1),
    (STEP_FINISH, SIGNAL_ADVANCE, 0),
)

# ---------------------------------------------------------------------------
# Tuning
# ---------------------------------------------------------------------------
# How far the Toon must walk to clear the movement step.
MOVE_START_FORWARD = 18.0
MOVE_MARKER_RADIUS = 7.0
# Practice Cogs are deliberately flimsy so the first shot lands quickly.
PRACTICE_COG_LEVEL = 1
PRACTICE_COG_HEALTH_FRACTION = 0.5
PRACTICE_COG_KILLS = 1
# Loadout step: this many tracks must be equipped.
LOADOUT_TARGET = 2
# Dodge step.
DODGE_ATTEMPTS = 3
DODGE_TELEGRAPH_TIME = 2.2
DODGE_MOVE_DISTANCE = 3.0
DODGE_DAMAGE = 2
# AI polling cadence for position / loadout checks.
POLL_INTERVAL = 0.4
# A tutorial Cog that has not been shot for this long is reconsidered.
FIRING_TIMEOUT = 60.0
# Rewards.
STEP_BEANS = 25
FINISH_BEANS = 250
FINISH_MASTERY = 60
# Jellybeans handed to a fresh Toon so the economy is explained on day one.
STARTER_BEANS = 100
# Coins: index is the step, value of the wire "value" field for hints.
NO_VALUE = 0


def stepIsExplanatory(step):
    """Steps that only advance when the player presses Continue."""
    return step in (STEP_WELCOME, STEP_FINISH)


def getRequirement(step):
    if 0 <= step < NUM_STEPS:
        return _STEP_REQUIREMENTS[step]
    return (step, None, 0)


class ActionTutorialState(object):
    """The authoritative progress of one Toon's tutorial.

    ``report`` feeds a gameplay signal in and returns True only when that
    signal cleared the current step and moved the machine forward.
    """

    __slots__ = ('step', 'cleared', 'values', 'finished')

    def __init__(self, step=STEP_WELCOME, cleared=None, values=None, finished=False):
        self.step = int(step)
        self.cleared = list(cleared or [])
        self.values = dict(values or {})
        self.finished = bool(finished) or self.step >= NUM_STEPS

    # -- queries ---------------------------------------------------------
    def getStep(self):
        return min(self.step, NUM_STEPS)

    def getTotal(self):
        return NUM_STEPS

    def isFinished(self):
        return self.finished

    def isCleared(self, step):
        return step in self.cleared

    def getValue(self, key, default=NO_VALUE):
        return self.values.get(key, default)

    def getRequirement(self):
        return getRequirement(self.step)

    def requiresAdvance(self):
        return stepIsExplanatory(self.getStep()) and not self.finished

    def getProgress(self):
        if self.finished:
            return NUM_STEPS, NUM_STEPS
        return self.getStep() + 1, NUM_STEPS

    def toWire(self):
        return [self.getStep(), NUM_STEPS]

    # -- mutation --------------------------------------------------------
    def setValue(self, key, value):
        self.values[key] = value

    def report(self, signal, value=0):
        """Feed a signal; return True when it advances the machine."""
        if self.finished:
            return False
        step, requiredSignal, minimum = self.getRequirement()
        if signal != requiredSignal:
            return False
        # Remember partial progress (for example tracks equipped so far).
        if signal == SIGNAL_LOADOUT:
            self.values[SIGNAL_LOADOUT] = int(value)
        if value < minimum:
            return False
        self.values[signal] = int(value)
        if step not in self.cleared:
            self.cleared.append(step)
        self.step = step + 1
        if self.step >= NUM_STEPS:
            self.step = NUM_STEPS
            self.finished = True
        return True

    def __repr__(self):
        return 'ActionTutorialState(step=%d/%d, cleared=%r, finished=%s)' % (
            self.getStep(), NUM_STEPS, self.cleared, self.finished)


def makeProgressPercent(state):
    step, total = state.getProgress()
    return int(round(100.0 * step / float(total)))
