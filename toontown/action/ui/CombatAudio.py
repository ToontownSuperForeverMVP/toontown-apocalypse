"""Rate-limited feedback using the shipped Toontown sound library."""


class CombatAudio:
    CUES = {
        'select': ('phase_5/audio/sfx/GUI_battleselect.ogg', 0.35, 0.08),
        'hit': ('phase_4/audio/sfx/Seltzer_squirt_2dgame_hit.ogg', 0.25, 0.12),
        'dodge': ('phase_3/audio/sfx/GUI_rollover.ogg', 0.45, 0.4),
        'reward': ('phase_4/audio/sfx/SZ_DD_treasure.ogg', 0.5, 0.3),
        'complete': ('phase_3.5/audio/sfx/tt_s_gui_sbk_cdrSuccess.ogg', 0.6, 0.5),
        'warning': ('phase_3.5/audio/sfx/GUI_whisper_3.ogg', 0.45, 2.0),
        'ready': ('phase_5/audio/sfx/GUI_battlerollover.ogg', 0.2, 0.25),
    }

    def __init__(self):
        self.sounds = {}
        self.lastPlayed = {}

    def play(self, cue):
        path, volume, interval = self.CUES[cue]
        now = globalClock.getFrameTime()
        if now - self.lastPlayed.get(cue, -float('inf')) < interval:
            return
        self.lastPlayed[cue] = now
        if cue not in self.sounds:
            loader = getattr(base, 'loader', None)
            self.sounds[cue] = loader.loadSfx(path) if loader else None
        sound = self.sounds[cue]
        if sound:
            sound.setVolume(volume)
            sound.play()

    def destroy(self):
        for sound in self.sounds.values():
            if sound:
                sound.stop()
        self.sounds.clear()
        self.lastPlayed.clear()
