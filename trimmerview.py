from AppKit import (
    NSView, 
    NSColor, 
    NSBezierPath,
    NSRectFill,
    NSMakeRect,
    NSTrackingArea,
    NSTrackingMouseEnteredAndExited,
    NSTrackingActiveAlways,
    NSTrackingInVisibleRect,
    NSViewWidthSizable,
    NSViewMaxYMargin,
    NSGraphicsContext
)

from CoreMedia import CMTimeGetSeconds

from AVFoundation import (
    AVAsset,
    AVAssetImageGenerator,
    AVPlayerItem
)
from Foundation import (
    NSPoint,
    NSSize
)
import objc

class TrimmerView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(TrimmerView, self).initWithFrame_(frame)
        if self:
            self.asset = None
            self.duration = 0
            self.start_time = 0
            self.end_time = 0
            self.is_dragging_start = False
            self.is_dragging_end = False
            self.handle_width = 10
            
            # For generating thumbnails
            self.image_generator = None
            
            # Set up tracking area for mouse events
            tracking_options = (NSTrackingMouseEnteredAndExited | 
                             NSTrackingActiveAlways | 
                             NSTrackingInVisibleRect)
            
            tracking_area = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                self.bounds(),
                tracking_options,
                self,
                None
            )
            self.addTrackingArea_(tracking_area)
        return self

    def setAsset_(self, asset):
        self.asset = asset
        self.duration = CMTimeGetSeconds(asset.duration())
        self.end_time = self.duration        
        # Set up image generator for thumbnails
        self.image_generator = AVAssetImageGenerator.assetImageGeneratorWithAsset_(asset)
        self.image_generator.setAppliesPreferredTrackTransform_(True)
        
        self.setNeedsDisplay_(True)

    def drawRect_(self, dirtyRect):
        objc.super(TrimmerView, self).drawRect_(dirtyRect)
        
        if not self.asset:
            return
            
        # Get current context
        context = NSGraphicsContext.currentContext()
        if not context:
            return
            
        bounds = self.bounds()
        
        # Draw background
        NSColor.darkGrayColor().set()
        path = NSBezierPath.bezierPathWithRect_(bounds)
        path.fill()
        
        # Draw timeline
        timeline_rect = NSMakeRect(
            self.handle_width,
            bounds.size.height / 4,
            bounds.size.width - (2 * self.handle_width),
            bounds.size.height / 2
        )
        NSColor.lightGrayColor().set()
        timeline_path = NSBezierPath.bezierPathWithRect_(timeline_rect)
        timeline_path.fill()
        
        # Draw trim handles
        start_x = self.timeToX_(self.start_time)
        end_x = self.timeToX_(self.end_time)
        
        # Start handle
        start_rect = NSMakeRect(
            start_x - self.handle_width/2,
            0,
            self.handle_width,
            bounds.size.height
        )
        NSColor.whiteColor().set()
        start_path = NSBezierPath.bezierPathWithRect_(start_rect)
        start_path.fill()
        
        # End handle
        end_rect = NSMakeRect(
            end_x - self.handle_width/2,
            0,
            self.handle_width,
            bounds.size.height
        )
        end_path = NSBezierPath.bezierPathWithRect_(end_rect)
        end_path.fill()

    def timeToX_(self, time):
        if self.duration == 0:
            return self.handle_width
        
        usable_width = self.bounds().size.width - (2 * self.handle_width)
        x = self.handle_width + (time / self.duration) * usable_width
        return x

    def xToTime_(self, x):
        usable_width = self.bounds().size.width - (2 * self.handle_width)
        time = ((x - self.handle_width) / usable_width) * self.duration
        return max(0, min(self.duration, time))

    def mouseDown_(self, event):
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        x = point.x
        
        # Check if clicking near handles
        start_x = self.timeToX_(self.start_time)
        end_x = self.timeToX_(self.end_time)
        
        if abs(x - start_x) < self.handle_width:
            self.is_dragging_start = True
        elif abs(x - end_x) < self.handle_width:
            self.is_dragging_end = True

    def mouseDragged_(self, event):
        if not (self.is_dragging_start or self.is_dragging_end):
            return
            
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        time = self.xToTime_(point.x)
        
        if self.is_dragging_start:
            self.start_time = min(time, self.end_time)
        elif self.is_dragging_end:
            self.end_time = max(time, self.start_time)
            
        self.setNeedsDisplay_(True)

    def mouseUp_(self, event):
        self.is_dragging_start = False
        self.is_dragging_end = False