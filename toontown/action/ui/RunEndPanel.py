"""Extraction and game-over report for a real-time street run."""

from panda3d.core import TextNode, Vec4
from direct.gui.DirectGui import DirectButton, DirectFrame, DGG, OnscreenText
from direct.interval.IntervalGlobal import LerpColorScaleInterval, LerpScaleInterval, Parallel, Sequence, Wait
from direct.showbase.DirectObject import DirectObject

from toontown.toonbase import TTLocalizer, ToontownGlobals


class RunEndPanel(DirectObject):
    """A self-contained overlay so place teardown cannot eat the run report."""

    def __init__(self, streetName, summary, escaped, doneCallback=None):
        DirectObject.__init__(self)
        self.doneCallback = doneCallback
        self.finished = False
        self.signFont = ToontownGlobals.getSignFont()
        self.uiFont = ToontownGlobals.getInterfaceFont()
        self._build(streetName, summary, escaped)
        self.accept('escape', self.close)
        self.accept('enter', self.close)

    def _build(self, streetName, summary, escaped):
        color = Vec4(0.4, 0.95, 0.65, 1) if escaped else Vec4(1.0, 0.38, 0.32, 1)
        title = TTLocalizer.ActionEndEscapedTitle if escaped else TTLocalizer.ActionEndGameOverTitle
        subtitle = TTLocalizer.ActionEndEscapedSub if escaped else TTLocalizer.ActionEndGameOverSub
        self.backdrop = DirectFrame(parent=aspect2d, relief=DGG.FLAT, frameSize=(-2, 2, -1.2, 1.2),
                                    frameColor=(0.015, 0.02, 0.05, 0.92))
        self.backdrop.setBin('gui-popup', 125)
        self.frame = DirectFrame(parent=self.backdrop, relief=DGG.FLAT, frameSize=(-0.78, 0.78, -0.6, 0.6),
                                 frameColor=(0.055, 0.075, 0.12, 0.98))
        self.frame.setColorScale(1, 1, 1, 0)
        self.frame.setScale(0.82)
        OnscreenText(parent=self.frame, text=title, pos=(0, 0.43), scale=0.1, fg=color,
                     shadow=(0, 0, 0, 1), font=self.signFont)
        OnscreenText(parent=self.frame, text=streetName.upper(), pos=(0, 0.34), scale=0.048,
                     fg=(1, 1, 1, 0.88), shadow=(0, 0, 0, 1), font=self.uiFont)
        OnscreenText(parent=self.frame, text=subtitle, pos=(0, 0.265), scale=0.037,
                     fg=(0.82, 0.86, 0.95, 0.92), font=self.uiFont)
        rows = ((TTLocalizer.ActionEndKills, summary.get('kills', 0)),
                (TTLocalizer.ActionEndBeans, '+%d' % summary.get('beans', 0)),
                (TTLocalizer.ActionEndObjectives, summary.get('objectives', 0)),
                (TTLocalizer.ActionEndDodges, summary.get('dodges', 0)),
                (TTLocalizer.ActionEndDamage, summary.get('damage', 0)),
                (TTLocalizer.ActionEndPressure, summary.get('pressure', 0)),
                (TTLocalizer.ActionEndTime, self._formatTime(summary.get('seconds', 0))))
        for index, (label, value) in enumerate(rows):
            y = 0.16 - index * 0.095
            DirectFrame(parent=self.frame, relief=DGG.FLAT, frameSize=(-0.63, 0.63, -0.034, 0.034),
                        frameColor=(color[0] * 0.12, color[1] * 0.12, color[2] * 0.12, 0.68), pos=(0, 0, y))
            OnscreenText(parent=self.frame, text=label, pos=(-0.58, y - 0.014), scale=0.034,
                         fg=(0.85, 0.9, 1, 0.95), align=TextNode.ALeft, font=self.uiFont)
            OnscreenText(parent=self.frame, text=str(value), pos=(0.58, y - 0.016), scale=0.045,
                         fg=color, align=TextNode.ARight, font=self.signFont)
        self.button = DirectButton(parent=self.frame, relief=DGG.FLAT, text=TTLocalizer.ActionEndContinue,
                                   text_font=self.signFont, text_scale=0.05, text_fg=(1, 1, 1, 1),
                                   frameSize=(-0.23, 0.23, -0.055, 0.055), frameColor=(color[0], color[1], color[2], 0.9),
                                   pos=(0, 0, -0.5), command=self.close)
        self.track = Parallel(LerpColorScaleInterval(self.frame, 0.25, Vec4(1, 1, 1, 1)),
                              LerpScaleInterval(self.frame, 0.3, 1.0, blendType='easeOut'))
        self.track.start()

    @staticmethod
    def _formatTime(seconds):
        return '%d:%02d' % (int(seconds) // 60, int(seconds) % 60)

    def close(self):
        if self.finished:
            return
        self.finished = True
        callback = self.doneCallback
        self.destroy()
        if callback:
            callback()

    def destroy(self):
        self.ignoreAll()
        if getattr(self, 'track', None):
            self.track.finish()
            self.track = None
        if getattr(self, 'backdrop', None):
            self.backdrop.destroy()
            self.backdrop = None
