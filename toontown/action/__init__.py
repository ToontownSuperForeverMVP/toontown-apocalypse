"""Toontown Apocalypse real-time street combat.

Streets are replayable levels: roaming Cogs hunt the Toon in real time, the
player picks a difficulty tier on entry, and staying on a street ratchets up
"Cog Pressure".  Gag tracks are found in levels, carried three at a time and
upgraded through three tiers with jellybeans.

Package layout::

    ActionGlobals        shared constants and pure balance formulas
    ActionProgression    gag-track tier / mastery / loadout helpers
    cog/                 real-time Cog attack registry + AI brain
    director/            pressure model + per-street director (AI)
    objectives/          procedural street contracts
    ui/                  HUD, tier select and loadout panels (client)
    StreetRun            client-side glue used by Street.py
"""
