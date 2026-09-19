# Real-time street combat (Toontown Apocalypse)

Streets are no longer turn-based battle arenas. Every street is a repeatable
first-person combat level with a selectable difficulty tier, a rolling
"Cog Pressure" meter, procedurally generated contracts and a three-track gag
loadout. Playgrounds stay normal, safe Toontown spaces. Cog Buildings, bosses,
factories and other legacy battle content are **untouched** and still run the
classic `DistributedBattle*` code.

The core loop:

```text
PLAYGROUND  (loadout, shops, tier-free)
    ↓ tunnel
STREET      pick a tier (1-10) → run starts
            fight roaming Cogs in real time
            pressure rises the longer you stay
            complete up to 3 procedural contracts
            find Gag Caches → discover new tracks
    ↓ leave / go sad
PLAYGROUND  bank rewards, equip tracks, buy gag tiers
```

## Package layout

```text
toontown/action/
  ActionGlobals.py            all tunables: tiers, pressure, gags, rewards, power rating, difficulty profile
  ActionProgression.py        helpers shared by client/AI: loadout normalisation, tier ownership, purchase rules
  StreetRun.py                client glue used by Street.py (tier prompt, HUD, run start/stop, death routing)
  DistributedTrackCache[AI].py  Gag Cache pickup that permanently discovers a track
  cog/
    CogAttackRegistry.py      every legacy SuitAttackType as a real-time attack (shape, range, windup, mutations)
    CogCombatControllerAI.py  per-Cog brain: PATROL → ALERT → ENGAGE → STAGGER/LURED → DEFEATED/DEPARTING
    CogAttackFX.py            client telegraphs, projectiles, beams, AoE rings, death FX
  director/
    PressureDirector.py       the rolling Cog Pressure meter (pure logic, unit tested)
    StreetDirectorAI.py       one per street planner: run state, tier, pressure, spawning, rewards, objectives,
                              traps, toon-ups, caches, engage slots
  objectives/
    ObjectiveGenerator.py     procedural contracts (kill N of dept, defeat elites, survive stage, ...)
  ui/
    ActionHUD.py              tier/pressure/objectives, toasts, hit marker, damage vignette
    TierSelectPanel.py        overlay shown when entering a street
    LoadoutPanel.py           "Gag Album": equip up to three tracks, buy tiers with jellybeans
  tutorial/
    ActionTutorialGlobals.py  the seven tutorial steps as pure data + ActionTutorialState
    ActionTutorialManagerAI.py the AI manager and the per-Toon session (spawns, signals, rewards)
    TutorialPracticeSuitAI.py the flimsy stationary practice Cog
    ActionTutorialManager.py  client mirror of the replicated step
    ActionTutorialCoach.py    the on-screen coach panel (steps, hints, Continue, Skip)
tools/
  action_selftest.py          pure-python harness for the logic modules + dc parse
  action_fulltest.py          exhaustive headless suite for every street and tutorial feature
  action_dc_check.py          static dc ↔ Python handler / sendUpdate arity checker
docs/ACTION_COMBAT.md         this file
```

## Difficulty model

Three independent values drive every encounter:

| Value | Where it lives | Meaning |
| --- | --- | --- |
| **Selected tier** (1-10) | `StreetDirectorAI.tier`, chosen in `TierSelectPanel` | Intentional baseline challenge. |
| **Player power** | `ActionGlobals.computePlayerPower` style helpers (`TRACK_TIER_POWER`, laff, mastery) | Persistent strength of the Toon. Tier 10 with Throw I is nasty *for that kit*; it gets nastier as the kit grows, but never fully normalises. |
| **Cog Pressure** (0-1000) | `PressureDirector.PressureMeter` | Transient. Rises with time, kills, elites, objectives and dodges; decays when nobody is on the street; halves when a Toon goes sad. |

`ActionGlobals.getDifficultyProfile(tier, playerPower, pressure)` folds them into
a `DifficultyProfile` (HP/damage scale kept modest; aggression, cooldowns,
mutation level, elite chance, spawn interval, population target and reward
scale do the heavy lifting).

Pressure stages (`PRESSURE_STAGE_THRESHOLDS`):

| Stage | Threshold | Reward × | Cog level offset |
| --- | --- | --- | --- |
| CALM | 0 | 1.00 | +0 |
| ACTIVE | 100 | 1.15 | +0 |
| ALERT | 220 | 1.35 | +1 |
| CRACKDOWN | 360 | 1.60 | +1 |
| LOCKDOWN | 520 | 1.90 | +2 |
| INVASION | 700 | 2.30 | +3 |

The street never becomes impossible: pressure only changes behaviour,
population and rewards, and the player can always walk out.

## Cogs

* Street Cogs spawn through the existing `DistributedSuitPlannerAI` path
  network; the director owns the population target while a run is active.
* When a Cog notices a Toon the controller takes movement authority
  (`DistributedSuitAI.beginActionControl` stops the DNA path) and streams
  positions with `setSmPosHpr` / `setSmStop`; the client `DistributedSuit`
  enters its `Action` state and smooths them.
* Attacks come from `CogAttackRegistry`: each legacy `SuitAttackType` has a
  shape (melee, projectile, beam, AoE), range band, windup, recovery, cooldown
  and a list of **difficulty mutations** (extra pulses, tracking, shorter
  recovery, movement during recovery...). Higher difficulty means harder
  patterns, not just bigger numbers.
* Hit resolution is AI-authoritative: `actionAttack` telegraphs, the AI checks
  the Toon's position/velocity at impact time, then `actionAttackResolved`
  reports the damage (0 = dodged).
* `requestFreeGagHit` validates the equipped track, owned tier, range and rate
  before applying damage; kills pay jellybeans + mastery XP scaled by pressure
  stage and tier (`KILL_BEANS_*`, `KILL_XP_*`, trivial targets pay less).

## Gags

* Seven tracks, **three tiers each**. `trackAccess[track]` now stores the owned
  tier (0 = undiscovered).
* Tracks are **discovered** only through Gag Caches (`DistributedTrackCache`)
  dropped by the director (`CACHE_SPAWN_CHANCE_*`, `CACHE_RESPAWN_KILLS`).
* Mastery XP (`setExperience`) comes only from successful hits. Reaching
  `TRACK_TIER_XP[tier]` makes the next tier *purchasable*; buying costs
  `TRACK_TIER_COST[tier]` jellybeans (`requestBuyGagTier`). XP is never lost.
* Up to `MAX_EQUIPPED_TRACKS` (3) tracks are equipped at a time
  (`setEquippedTracks` / `requestEquipTracks`, free, no XP loss). Slots are
  bound to `1` `2` `3`, the mouse wheel cycles, `mouse1` fires, `Tab` toggles
  first person, and `L` (`Settings.controls.LOADOUT_HOTKEY`) opens the Gag
  Album in any walkable place.
* `GagViewModel` renders the equipped weapon per track/tier; Trap and Toon-Up
  go through the planner (`requestActionTrap`, `requestActionToonUp`) because
  they are not aimed at a single Cog.

## Network contract (`astron/dclass/ttap.dc`)

* `DistributedToon`: `setEquippedTracks`, `requestEquipTracks`,
  `requestBuyGagTier`, `gagTierPurchaseResult`.
* `DistributedSuitPlanner`: `requestStreetRun`, `leaveStreetRun`,
  `setActionTier`, `setActionPressure`, `setActionObjectives`,
  `actionObjectiveComplete`, `actionAnnounce`, `requestActionTrap`,
  `actionTrapPlaced`, `actionTrapTriggered`, `requestActionToonUp`,
  `actionToonUp`.
* `DistributedSuitBase` (now `: DistributedSmoothNode`): `setActionState`,
  `actionAttack`, `actionAttackResolved`, `actionStatus`, `actionDefeated`.
* `DistributedTrackCache : DistributedTreasure`: `setTrackReward`,
  `requestGrab`, `setGrab`.
* `ActionTutorialManager`: `requestAdvance`, `requestSkip` (client -> AI),
  `setTutorialStep`, `setTutorialValue`, `tutorialStepComplete`,
  `tutorialFinished` (AI -> client), plus `setTutorialAck` on `DistributedToon`.

`DistributedAvatarAI` derives from `DistributedSmoothNodeAI` so the AI applies
the `airecv` smooth-position updates it receives; the director relies on
`toon.getPos()` being meaningful on the AI.

## Tutorial (right after Make-A-Toon)

A brand new Toon is created with `tutorialAck = 0`, so login routes through
`tutorialQuestion` -> `requestTutorial`, exactly as the old tutorial did. What
changed is what happens on arrival:

1. `TutorialManagerAI` allocates four private zones (branch, street, shop, HQ)
   and tells the client to load them; `ZoneUtil.overrideOn` keeps the Toon on
   that private street and `cantLeaveGame` locks the exit.
2. `PlayGame.enterTutorialHood` fires `toonArrivedTutorial`, so
   `TutorialManagerAI.toonArrived` normalises the Toon (quests, reward history,
   15 laff, full inventory, zero gag XP) and hands off to
   `ActionTutorialManagerAI.startSession`.
3. The session grants the starter kit (Throw I, default loadout, jellybean
   wallet) and walks `ActionTutorialState` through seven steps: Welcome,
   Move, Fire, Gag Album, Dodge, Gag Cache, Graduation. Movement, loadout and
   dodge steps are observed from the Toon's real position/fields; the practice
   Cog and the tutorial Gag Cache are spawned a few feet ahead of the player.
   A player who struggles is never stuck: the dodge step forgives the attempt
   after `DODGE_ATTEMPTS` tries.
4. `TierSelectPanel`/`StreetRun` are **not** used on the tutorial street
   (`Street.isTutorialStreet()`), so the coach never competes with the tier
   prompt for the screen.
5. `tutorialFinished` tears the coach down, teleports the Toon to their
   `defaultZone` playground (falling back to Toontown Central), then releases
   `cantLeaveGame`, `ZoneUtil.overrideOff()` and the private zones. A skipped
   tutorial takes the same path and still sets `tutorialAck`, so it is never
   offered twice. `~skiptutorial` skips it from a chat line during testing.

The lesson also graduates the player: the final step pays beans and mastery XP
into the Toon's first equipped track.

## Combat feedback and defeat chains

Defeat another Cog within **8 seconds** without taking Cog attack damage to
extend a personal chain. Each additional defeat adds **5%** to its jellybean
and mastery reward, capped at **25%**. The AI computes the bonus; the existing
`actionAnnounce` message (kind `ANNOUNCE_KILL_CHAIN`) drives the local chain
readout and countdown. Damage, leaving the run, and run teardown clear the
chain. The first defeat has no bonus. Rewards retain the existing wallet cap.

The street HUD includes a smoothed laff bar with exact numeric health and a
low-laff warning, a three-second recent-hit target health readout, individual
contract progress bars, and native sound cues for combat and rewards. Cues
are cached per HUD and rate-limited, and respect the game's sound system.
Reward toasts have bounded node/interval lifetimes and retain their stack
positions when several rewards arrive together.

Melee and beam tells show their locked cones. Cogs retain that direction
during wind-up, and impact visuals retain the difficulty-mutated radius.
World-space rings avoid inheriting Cog model scale.

`Panda3D/python/ppython.exe -S tools/action_hud_render.py` renders the real
DirectGUI with bundled fonts to `screenshots/action-hud-preview.png`, verifies
cue asset paths, stresses toast expiration, and checks HUD task cleanup.
It uses synthetic replicated state and silent audio; it is not a live combat
playtest or an audible sound check.

## Street events, extraction, and game over

Every 12 street-wide Cog defeats triggers a **Street Breakthrough**. Every
active Toon receives a pressure-scaled jellybean reward, and Cog Pressure drops
by 65. It is a shared tempo reset: a group can deliberately push into the next
reward threshold, while a solo player gets a small breather during long runs.
The server owns both the payout and the pressure change.

Tier 2 and above may post a **Cog Chain** contract. It completes when the Toon
reaches the requested consecutive-defeat count during the existing eight-second
chain window. Taking Cog damage resets the chain and cannot preserve progress.

Leaving a started street run opens an extraction report. Going sad opens a
game-over report before returning to the playground; Continue, Enter, or Escape
acknowledges it. Reports snapshot local run feedback: defeats, beans earned,
contracts, dodges, damage, peak pressure, and duration. The player’s permanent
rewards are awarded by the server before the report is shown.

`Panda3D/python/ppython.exe -S tools/action_end_screen_render.py` renders both
end-screen modes, validates the report lifecycle, and writes
`screenshots/action-game-over-preview.png`.

## Developer magic words

| Word | Effect |
| --- | --- |
| `~tier N` | set the tier of the street you stand on |
| `~pressure N` | set Cog Pressure (0-1000) |
| `~discover <track|all>` | discover a track as if a cache was opened |
| `~gagtier <track> <0-3>` | set the owned tier, skipping the cost |
| `~mastery <track> <xp>` | set mastery XP |
| `~equip throw squirt sound` | equip up to three discovered tracks |
| `~cache` | spawn a Gag Cache on the current street |
| `~skiptutorial` | finish the guided tutorial you are in right now |

## Validation

```text
./Panda3D/python/ppython.exe tools/action_selftest.py   # logic modules + dc parse
./Panda3D/python/ppython.exe tools/action_dc_check.py   # dc field arity vs. handlers / sendUpdate literals
./Panda3D/python/ppython.exe tools/action_fulltest.py   # every street + tutorial feature (6800+ checks)
```

All three run without booting a client or an AI. Add new dc fields to
`tools/action_dc_check.py` and cover them in `tools/action_fulltest.py` when
extending the contract. The full suite stubs DirectGui, the clock, tasks and
the messenger so the panels, the HUD, the coach and the AI managers can be
driven deterministically.

## Not in this pass (next steps)

* Cog Buildings as escalating roguelite towers (elevator = risk/reward room).
* A Toon-flavoured style/rank meter multiplying mastery XP.
* Extra mobility (slide, dash, air control) beyond the existing sprint/crouch.
* Playtest-driven tuning of every constant in `ActionGlobals.py`.
