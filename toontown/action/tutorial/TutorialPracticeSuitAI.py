"""A flimsy, stationary Cog spawned by the action tutorial.

It reuses :class:`DistributedTutorialSuitAI` (already a distributed class the
client renders) but with no suit planner, so the real-time hit path falls back
to the planner-less branch in ``DistributedSuitBaseAI.requestFreeGagHit``.  The
only extra behaviour is telling the tutorial session when it is defeated so the
FIRE step can advance.
"""

import random

from toontown.action.tutorial import ActionTutorialGlobals as G
from toontown.suit.DistributedTutorialSuitAI import DistributedTutorialSuitAI
from toontown.suit import SuitDNA

# Weak, easy-to-read Cog types for target practice.
PRACTICE_SUIT_NAMES = ('f', 'cc', 'sc', 'bgh')


class TutorialPracticeSuitAI(DistributedTutorialSuitAI):

    def __init__(self, air, session=None, name=None, level=None, healthFraction=None):
        DistributedTutorialSuitAI.__init__(self, air)
        self.tutorialSession = session
        self.practiceName = name or random.choice(PRACTICE_SUIT_NAMES)
        dna = SuitDNA.SuitDNA()
        dna.newSuit(self.practiceName)
        self.dna = dna
        self.setLevel(level or G.PRACTICE_COG_LEVEL)
        fraction = G.PRACTICE_COG_HEALTH_FRACTION if healthFraction is None else healthFraction
        self.maxHP = max(1, int(round(self.maxHP * fraction)))
        self.currHP = self.maxHP

    def generate(self):
        DistributedTutorialSuitAI.generate(self)
        # Street suits rely on the required field cache for this; send it
        # explicitly so a practice Cog is always visible.
        try:
            self.sendUpdate('setDNAString', [self.getDNAString()])
        except Exception:
            pass

    def _onActionDefeated(self, toon, gagDef):
        DistributedTutorialSuitAI._onActionDefeated(self, toon, gagDef)
        session = self.tutorialSession
        if session is not None:
            session.onPracticeCogDefeated(self)
