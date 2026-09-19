# Toontown Apocalypse

A singleplayer-first Toontown game that turns the classic turn-based battles into a first-person looter shooter with roguelite street runs. WIP.

This project is built on a cleaned-up Toontown source base with the original multiplayer infrastructure retained for local hosting and mini-servers.

## AI Disclosure

> [!IMPORTANT]
> A majority of the code in this repository is **AI-assisted**. It is written with the help of AI coding tools and then **reviewed by a human** before being committed. AI accelerates the work; a human remains responsible for reviewing, validating, and owning every change that lands here.

## Source Code

This source code is based on a March 2019 fork of Toontown Offline v1.0.0.0 used for Toontown School House.
It has been stripped of all Toontown Offline exclusive features, save one. The brand new Magic Words system made for
Toontown Offline has been left alone, and upgraded to the most recent build. This feature will allow users to easily navigate around Toontown without any hassle.

On top of that, this source code has also been updated to Python 3, utilizing a more modern version of Panda3D.

## Current Features

### Real-time street combat (the core loop)

Streets are repeatable first-person combat levels instead of turn-based battle arenas:

* Walking into a street prompts for a **difficulty tier (1–10)**. Tiers scale relative to your Toon's power, so tier 10 is brutal with Throw I alone and stays demanding as you grow.
* **Cog Pressure** rises the longer you stay (CALM → ACTIVE → ALERT → CRACKDOWN → LOCKDOWN → INVASION): more Cogs, nastier attack patterns, and up to **2.3x** rewards. You can always leave; the street never becomes impossible.
* Cogs roam, spot you, and attack in real time with telegraphed, dodgeable versions of their classic attacks — melee, projectiles, beams, and AoE rings, with difficulty mutations at higher tiers (extra pulses, tracking, shorter recoveries).
* First-person aiming and movement built for combat: **sprint**, **crouch**, **jump**, mouse-wheel loadout cycling, and instant `Tab` toggling between first and third person.
* Every street ends in an **extraction report** (or a game-over report if you go sad) showing defeats, beans earned, contracts, dodges, damage taken, peak pressure, and run duration.

### Gags as weapons

* Seven gag tracks, **three tiers each**, used as real-time weapons via a first-person view model.
* Carry up to **three equipped tracks** at once: slots bound to `1`/`2`/`3`, mouse wheel to cycle, `mouse1` to fire, `L` to open the Gag Album, `Tab` to toggle first person.
* Tracks are **discovered from Gag Caches** found on streets — you do not start with them.
* Tracks **level up only through successful hits** (mastery XP), and once a tier's XP requirement is met you **buy the next tier with jellybeans**. XP is never lost on unequip.
* Trap and Toon-Up go through the street director because they are not aimed at a single Cog.

### Roguelite progression

* Each run hands out up to **three procedural contracts** (kill N of a department, defeat elites, survive a pressure stage, ...) with per-contract progress bars on the HUD. No story quests on streets.
* **Kill chains**: defeat another Cog within 8 seconds without taking Cog attack damage to build a chain worth up to **+25%** beans and mastery XP.
* **Street Breakthroughs** every 12 street-wide defeats pay every active Toon a pressure-scaled reward and drop Cog Pressure by 65 — a shared tempo reset.
* Jellybean wallet, mastery XP, and track tiers persist between runs; contracts and pressure are per-run.

### Combat feedback

* Real-time HUD: smoothed laff bar with exact health, recent-hit target health readout, tier/pressure/objective readouts, hit markers, damage vignette, reward toasts, and native sound cues.
* Telegraphs show locked cones for melee and beam attacks; impact visuals match the difficulty-mutated radius.

### Guided tutorial

A seven-step tutorial right after Make-A-Toon (Welcome, Move, Fire, Gag Album, Dodge, Gag Cache, Graduation) with an on-screen coach, a flimsy practice Cog, and a tutorial Gag Cache. It teaches the whole loop before your first real street run, and can be skipped with `~skiptutorial` during testing.

### Classic Toontown, intact

Playgrounds, Cog Buildings, factories, and bosses still use the classic systems and battles — only streets have been converted. Mini-server hosting for playing with friends is also retained.

For the full architecture, tuning constants, network contract, and dev magic words (`~tier`, `~pressure`, `~discover`, `~gagtier`, `~equip`, `~cache`), see [`docs/ACTION_COMBAT.md`](docs/ACTION_COMBAT.md).

## Getting Started (Windows)

Windows is the only supported platform. For other platforms you will need to build the Panda3D engine yourself — see the build instructions [here](https://github.com/toontown-archipelago/panda3d). This is an advanced, unsupported use-case.

### Step 1: Install prerequisites

* **A GPU and drivers capable of running Panda3D** (any reasonably modern machine is fine)

You do **not** need a system Python: the game runs on a bundled interpreter shipped with the Panda3D runtime. Git is only required if you choose the Git/GitHub Desktop setup below.

### Step 2: Choose an install method

#### ZIP/local install

Download the repository as a ZIP from the GitHub **Code** button and extract it. This is the easiest option if you do not want to install Git. The extracted folder is your local game installation.

When you want to update a ZIP/local installation, double-click **`start.bat` in the project root**. It checks GitHub for the latest commit on `main`, downloads the updated source ZIP when needed, applies it while keeping local runtime/configuration files, and then starts the game. The updater needs an internet connection.

#### Git or GitHub Desktop install

Alternatively, install [Git](https://git-scm.com/downloads) and clone the repository:

```bat
git clone https://github.com/ToontownSuperForeverMVP/toontown-apocalypse.git
cd toontown-apocalypse
```

For a Git checkout, update the project with GitHub Desktop's **Pull origin** action or with plain Git:

```bat
git pull --ff-only origin main
```

After updating a Git/GitHub Desktop checkout, start the game directly with **`launch/windows/start-all.bat`**. That launcher starts the local servers and client; it does not download or replace the repository. Do not use the ZIP updater workflow for a Git checkout.

### Step 3: Download the Panda3D runtime

This source requires a specific Panda3D build. Download the latest engine build from the [releases page](https://github.com/toontown-archipelago/panda3d/releases/latest), then extract it so the interpreter ends up at:

```text
Panda3D/python/ppython.exe
```

i.e. a `Panda3D/` folder at the repository root containing `python/ppython.exe`. The runtime is intentionally ignored by Git because it is a local engine distribution rather than project source, so it will never appear in your commits.

### Step 4: Start the game

Use the launcher for your install type from step 2. For a ZIP/local install, use the root `start.bat` so the update check runs first. For a Git/GitHub Desktop checkout, use `launch/windows/start-all.bat` directly. Either launcher starts Astron, the UberDOG, the AI server, and the game client, and streams all of their output into one colored console. On first run it will ask for a player name and district name (defaults are fine) and save them to `launch/windows/launcher_config.json` — a local settings file that is ignored by Git. It also installs the Python dependencies from `requirements.txt` into the bundled interpreter on first run.

Wait for all four services to report ready, and the game window will open. Log in with the name you chose — you will go through Make-A-Toon and then the tutorial.

To start services individually instead, use the scripts under `build/scripts/` (`start_servers.bat`, `start_client.bat`) or the legacy ones under `win32/`.

### Step 5: Verify your install

Once in-game:

1. Complete the tutorial (or `~skiptutorial` from the chat line if you are impatient).
2. Walk into any street tunnel and pick a tier — try **1** first.
3. Defeat Cogs, open a Gag Cache, and press `L` to equip your tracks.

If you get the error `The system cannot find the path specified`, the Panda3D runtime is not where the launcher expects it — re-check step 3.

## Playing with friends (Mini-Server)

The unified launcher hosts a district on your machine. For friends to join:

* **They** set the *Game Server* IP in the launcher (or `TTOFF_GAME_SERVER`) to your public IP, and connect on port `7198`.
* **You** must allow incoming connections on port `7198`, either by port-forwarding it in your router's settings or by using a third-party program (such as Hamachi) to emulate a LAN connection over the internet.

As router settings are wildly different, we cannot provide a tutorial for your specific router here. However, the process is pretty straightforward assuming you have access to your router's settings — you should be able to figure it out with a bit of research.

## Reporting Issues

Found a crash, bug, or balance problem? Please open an issue on GitHub — it is the project's todo list.

1. **Go to the Issues page**: [github.com/ToontownSuperForeverMVP/toontown-apocalypse/issues](https://github.com/ToontownSuperForeverMVP/toontown-apocalypse/issues)
2. **Search first** — your issue may already be reported. If so, add a 👍 or extra details to the existing issue instead of opening a duplicate.
3. **Open a new issue** and include:
   * **What you were doing** — where in the game you were, what you clicked, what tier/pressure you were running.
   * **Steps to reproduce** — the exact sequence that triggers the bug, however random it seems.
   * **Expected vs actual behavior** — what should have happened, and what happened instead.
   * **Logs** — the console output of `launch/windows/start-all.bat` (copy the relevant section, especially anything with a traceback). Screenshots or clips help a lot.
   * **Your setup** — Windows version, and whether you are on the bundled launcher or running from source.
4. **Be descriptive** — "game crashed" is not actionable; "AI server traceback when a Level 4+ Cog fired a beam while I was crouched at pressure LOCKDOWN" usually is.

For crashes: note the last few things you did before the crash and check for a generated `errorCode` file or a traceback in the console — both are gold for debugging. District resets follow the same steps as a game crash.

## Credits

* [The Toontown Offline Team](https://ttoffline.com) for the foundation of this codebase (Toontown Schoolhouse)
* [The Corporate Clash Crew](https://corporateclash.net) for toon models, some various textures, and assistance with implementing v1.2.8 craning
* Polygon for making the Corporate Clash toon models
* [Open Toontown](https://github.com/open-toontown) for providing a great reference for a Toontown codebase ported to Python 3 and the HD Mickey Font
* Toontown Infinite for Bossbot HQ suit paths
* [Astron](https://github.com/Astron/Astron)
* [Panda3D](https://github.com/panda3d/panda3d) (More specifically, [Open Toontown's fork of Panda3D](https://github.com/open-toontown/panda3d))
* [libotp-nametags](https://github.com/loblao/libotp-nametags)
* [Ben Briggs](https://www.youtube.com/@benbriggsmusic) for the dripstinct music.
* [Project Bikehorn](https://github.com/toonjoey/toontown-project-bikehorn) created by toonjoey, for the textures made game ready from the Pandora leak.
* Reverse-engineered Toontown Online client/server source code is property of The Walt Disney Company.
