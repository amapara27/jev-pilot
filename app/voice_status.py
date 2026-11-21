"""Show voice progress in a nonactivating native panel; keep AppKit on the main thread."""

from threading import Lock

import AppKit as Cocoa
from Foundation import NSDate, NSRunLoop, NSTimer


WIDTH, PAD, ICON, RADIUS = 400, 16, 18, 14
# state → (title, SF Symbol, tint); tints are semantic system colors so they track appearance.
STATES = {'loading': ('Loading', 'hourglass', 'secondaryLabelColor'),
          'ready': ('Ready', 'mic', 'secondaryLabelColor'),
          'listening': ('Listening', 'waveform', 'systemRedColor'),
          'processing': ('Transcribing', 'text.bubble', 'controlAccentColor'),
          'working': ('Working', 'bolt.fill', 'controlAccentColor'),
          'approval': ('Approval needed', 'hand.raised.fill', 'systemOrangeColor'),
          'completed': ('Completed', 'checkmark.circle.fill', 'systemGreenColor'),
          'unheard': ('Try again', 'ear', 'secondaryLabelColor'),
          'failed': ('Command stopped', 'exclamationmark.circle.fill', 'systemRedColor'),
          'cancelled': ('Cancelled', 'xmark.circle', 'secondaryLabelColor')}


def label(size, weight, color):
    field = Cocoa.NSTextField.labelWithString_('')
    field.setFont_(Cocoa.NSFont.systemFontOfSize_weight_(size, weight))
    field.setTextColor_(getattr(Cocoa.NSColor, color)())
    return field


class VoiceStatus:
    """Coalesce worker updates and display readable state without stealing editor focus."""

    def __init__(self, *, enabled=True):
        self.lock = Lock()
        self.state, self.message = 'loading', 'Loading speech model…'
        self.panel = self.timer = None
        self.busy = False
        self.shown = None
        if not enabled:
            return
        Cocoa.NSApplication.sharedApplication()
        screen = Cocoa.NSScreen.mainScreen().visibleFrame()
        self.width = min(WIDTH, screen.size.width - 32)
        rect = Cocoa.NSMakeRect(screen.origin.x + (screen.size.width - self.width) / 2,
                                screen.origin.y + 28, self.width, 64)
        self.panel = Cocoa.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, Cocoa.NSWindowStyleMaskNonactivatingPanel | Cocoa.NSWindowStyleMaskBorderless,
            Cocoa.NSBackingStoreBuffered, False)
        self.panel.setReleasedWhenClosed_(False)
        self.panel.setLevel_(Cocoa.NSFloatingWindowLevel)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setIgnoresMouseEvents_(True)
        self.panel.setCollectionBehavior_(Cocoa.NSWindowCollectionBehaviorCanJoinAllSpaces
                                         | Cocoa.NSWindowCollectionBehaviorFullScreenAuxiliary)
        # A clear window lets the rounded material define the shape and the system shadow follow it.
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(Cocoa.NSColor.clearColor())
        self.panel.setHasShadow_(True)
        content = Cocoa.NSVisualEffectView.alloc().initWithFrame_(Cocoa.NSMakeRect(0, 0, self.width, 64))
        content.setMaterial_(Cocoa.NSVisualEffectMaterialHUDWindow)
        content.setBlendingMode_(Cocoa.NSVisualEffectBlendingModeBehindWindow)
        content.setState_(Cocoa.NSVisualEffectStateActive)
        content.setMaskImage_(self.mask())
        self.panel.setContentView_(content)
        self.icon = Cocoa.NSImageView.alloc().initWithFrame_(Cocoa.NSZeroRect)
        self.heading = label(13, Cocoa.NSFontWeightSemibold, 'labelColor')
        self.brand = label(11, Cocoa.NSFontWeightMedium, 'tertiaryLabelColor')
        self.brand.setStringValue_('Jev Pilot')
        self.brand.setAlignment_(Cocoa.NSTextAlignmentRight)
        self.detail = Cocoa.NSTextField.wrappingLabelWithString_('')
        self.detail.setFont_(Cocoa.NSFont.systemFontOfSize_(13))
        self.detail.setTextColor_(Cocoa.NSColor.secondaryLabelColor())
        self.detail.setMaximumNumberOfLines_(3)
        for view in (self.icon, self.heading, self.brand, self.detail):
            content.addSubview_(view)
        self.render()
        self.panel.orderFrontRegardless()
        self.timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.05, True, lambda _: self.render())
        self.pump()

    @staticmethod
    def mask():
        """Stretchable rounded mask: behind-window blur ignores layer corner radii."""

        side = RADIUS * 2 + 1
        image = Cocoa.NSImage.imageWithSize_flipped_drawingHandler_(
            Cocoa.NSMakeSize(side, side), False,
            lambda rect: Cocoa.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, RADIUS, RADIUS).fill() or True)
        image.setCapInsets_(Cocoa.NSEdgeInsetsMake(RADIUS, RADIUS, RADIUS, RADIUS))
        image.setResizingMode_(Cocoa.NSImageResizingModeStretch)
        return image

    def update(self, state, message=''):
        """Keep an active action or approval visible while more speech is captured."""

        with self.lock:
            if self.busy and state in {'listening', 'processing', 'unheard', 'cancelled'}:
                return
            self.busy = state in {'working', 'approval'}
            self.state, self.message = state, message

    def render(self):
        """Apply the latest state on AppKit's thread; full transcripts remain in Terminal."""

        if self.panel is None:
            return
        with self.lock:
            state, message = self.state, self.message
        if (state, message) == self.shown:
            return
        self.shown = state, message
        title, symbol, tint = STATES.get(state, (state, 'circle', 'secondaryLabelColor'))
        self.heading.setStringValue_(title)
        image = Cocoa.NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, title)
        self.icon.setImage_(image.imageWithSymbolConfiguration_(
            Cocoa.NSImageSymbolConfiguration.configurationWithPointSize_weight_(15, Cocoa.NSFontWeightSemibold)) if image else None)
        self.icon.setContentTintColor_(getattr(Cocoa.NSColor, tint)())
        compact = ' '.join(message.split())
        self.detail.setStringValue_(compact if len(compact) <= 150 else compact[:147] + '…')
        self.detail.setHidden_(not compact)
        self.detail.setAccessibilityHelp_(message)
        self.layout(bool(compact))
        self.panel.displayIfNeeded()

    def layout(self, has_detail):
        """Size the panel to its text, growing upward from a fixed bottom edge."""

        text_x = PAD + ICON + 10
        text_width = self.width - text_x - PAD
        detail_height = (self.detail.cell().cellSizeForBounds_(Cocoa.NSMakeRect(0, 0, text_width, 1000)).height
                         if has_detail else 0)
        height = 14 + (detail_height + 4 if has_detail else 0) + 18 + 14
        frame = self.panel.frame()
        if frame.size.height != height:
            frame.size.height = height
            self.panel.setFrame_display_(frame, True)
            self.panel.invalidateShadow()
        top = height - 14 - 18
        self.icon.setFrame_(Cocoa.NSMakeRect(PAD, top, ICON, ICON))
        self.heading.setFrame_(Cocoa.NSMakeRect(text_x, top, text_width - 72, 18))
        self.brand.setFrame_(Cocoa.NSMakeRect(self.width - PAD - 72, top + 1, 72, 16))
        self.detail.setFrame_(Cocoa.NSMakeRect(text_x, 14, text_width, detail_height))

    def pump(self):
        """Keep the panel responsive while the CLI waits for speech or approval."""

        if self.panel is not None:
            self.render()
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.date())

    def close(self):
        """Release the timer and panel on every session exit."""

        if self.timer is not None:
            self.timer.invalidate()
        if self.panel is not None:
            self.panel.close()
        self.timer = self.panel = None
