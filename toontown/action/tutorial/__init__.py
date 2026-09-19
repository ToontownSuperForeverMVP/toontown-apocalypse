"""Guided onboarding for Toontown Apocalypse's real-time street combat.

A brand-new Toon is taken to a private street and walked through the core loop
exactly once:

    WELCOME -> MOVE -> FIRE -> LOADOUT -> DODGE -> CACHE -> FINISH

The AI owns the authoritative :class:`~.ActionTutorialGlobals.ActionTutorialState`
and spawns practice Cogs and a Gag Cache for the player to interact with; the
client mirrors the step over the wire and renders the coach panel.

Package layout::

    ActionTutorialGlobals      pure step machine + tuning (no Panda globals)
    ActionTutorialManagerAI    AI manager, per-Toon sessions, practice spawning
    TutorialPracticeSuitAI     a flimsy, attackable Cog used for target practice
    ActionTutorialManager      client distributed object (mirrors the step)
    ActionTutorialCoach        client coach panel (steps, hints, skip)
"""
