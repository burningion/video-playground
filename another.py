import sys
import time
import numpy as np
from Foundation import NSURL, NSNotificationCenter, NSRect, NSPoint, NSSize
from AVKit import AVPlayerView

from AVFoundation import (
    AVAsset, 
    AVPlayer, 
    AVPlayerItem,
    AVPlayerItemVideoOutput,
    AVPlayerItemDidPlayToEndTimeNotification  
)
from Quartz import (
    kCVPixelBufferPixelFormatTypeKey,
    kCVPixelFormatType_32BGRA,
    CVPixelBufferLockBaseAddress,
    CVPixelBufferUnlockBaseAddress,
    CVPixelBufferGetWidth,
    CVPixelBufferGetHeight,
    CVPixelBufferGetBytesPerRow,
    CVPixelBufferGetBaseAddress
)
from Cocoa import (
    NSObject,
    NSWindow,
    NSView,
    NSScreen,
    NSApplication,
    NSButton,
    NSMakeRect,
    NSBezelStyleRounded,
    NSWindowStyleMaskTitled,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSBackingStoreBuffered,
    NSApplicationActivationPolicyRegular,
    NSViewWidthSizable,
    NSViewHeightSizable,
    NSViewMaxYMargin
)
import objc
from CoreMedia import CMTimeMake

from trimmerview import TrimmerView

def setShowsTrimControls_(self, value):
    pass

def setCanShowTrimControls_(self, value):
    pass

def setTrimmingMode_(self, value):
    pass

# Create proper selectors
objc.classAddMethods(AVPlayerView, [
    objc.selector(setShowsTrimControls_, selector=b'setShowsTrimControls:', 
                 signature=b'v@:Z', isClassMethod=False),
    objc.selector(setCanShowTrimControls_, selector=b'setCanShowTrimControls:', 
                 signature=b'v@:Z', isClassMethod=False),
    objc.selector(setTrimmingMode_, selector=b'setTrimmingMode:', 
                 signature=b'v@:Z', isClassMethod=False)
])

class VideoView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(VideoView, self).initWithFrame_(frame)
        if self is None: return None
        # Create an AVPlayerLayer to display the video
        self.player_layer = None
        # Make the view layer-backed
        self.setWantsLayer_(True)
        return self
    def setPlayer_(self, player):
        # Create AVPlayerView
        self.player_view = AVPlayerView.alloc().init()
        self.player_view.setPlayer_(player)
        self.player_view.setFrame_(self.bounds())
        self.player_view.setShowsTimecodes_(True)
        self.player_view.setShowsFrameSteppingButtons_(True)
        self.player_view.setCanShowTrimControls_(True)
        self.player_view.setShowsTrimControls_(True)
        # Make player view resize with parent view
        self.player_view.setAutoresizingMask_(
            NSViewWidthSizable | NSViewHeightSizable
        )
        
        # Add player view as subview
        self.addSubview_(self.player_view)

class WindowDelegate(NSObject):
    def windowWillClose_(self, notification):
        NSApplication.sharedApplication().terminate_(None)

class VideoPlayer(NSObject):
    def init(self):
        self = objc.super(VideoPlayer, self).init()
        if self is None: return None
        
        screen = NSScreen.mainScreen()
        screen_rect = screen.frame()
        window_width = 800
        window_height = 600
        
        window_rect = NSRect(
            origin=NSPoint(
                x=(screen_rect.size.width - window_width) / 2,
                y=(screen_rect.size.height - window_height) / 2
            ),
            size=NSSize(width=window_width, height=window_height)
        )
        
        # Add standard window style mask
        style_mask = (
            NSWindowStyleMaskTitled |
            NSWindowStyleMaskClosable |
            NSWindowStyleMaskMiniaturizable |
            NSWindowStyleMaskResizable
        )
        
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            window_rect,
            style_mask,
            NSBackingStoreBuffered,
            False
        )
        
        # Set window properties
        self.window.setTitle_("Video Player")
        
        # Set up window delegate
        self.window_delegate = WindowDelegate.alloc().init()
        self.window.setDelegate_(self.window_delegate)
        
        # Create controls
        control_height = 40
        content_rect = self.window.contentView().frame()
        
        # Create video view slightly smaller to make room for controls
        video_rect = NSRect(
            origin=NSPoint(x=0, y=control_height),
            size=NSSize(
                width=content_rect.size.width,
                height=content_rect.size.height - control_height
            )
        )
        self.view = VideoView.alloc().initWithFrame_(video_rect)
        
        # Create control buttons
        ''' # hide play button for now
        button_width = 80
        button_height = 30
        button_y = 5
        
        play_button = NSButton.alloc().initWithFrame_(
            NSRect(
                origin=NSPoint(x=10, y=button_y),
                size=NSSize(width=button_width, height=button_height)
            )
        )
        play_button.setTitle_("Play/Pause")
        play_button.setBezelStyle_(NSBezelStyleRounded)
        play_button.setTarget_(self)
        play_button.setAction_(self.togglePlayPause_)
        '''
        #self.window.contentView().addSubview_(play_button) # hide play button for now
        trimmer_frame = NSMakeRect(0, 0, self.window.frame().size.width, 100)
        self.trimmer_view = TrimmerView.alloc().initWithFrame_(trimmer_frame)
        self.window.contentView().addSubview_(self.trimmer_view)
        
        # Position trimmer at bottom
        self.trimmer_view.setAutoresizingMask_(
            NSViewWidthSizable | NSViewMaxYMargin
        )
        self.window.contentView().addSubview_(self.view)

        self.window.makeKeyAndOrderFront_(None)
        
        self.current_frame = None
        self.is_playing = False
        self.player = None
        self.player_item = None
        self.output = None
        return self

    def togglePlayPause_(self, sender):
        if self.is_playing:
            self.pause()
        else:
            self.play()
    
    def load_video(self, path):
        url = NSURL.fileURLWithPath_(path)
        asset = AVAsset.assetWithURL_(url)
        self.player_item = AVPlayerItem.playerItemWithAsset_(asset)
        self.player = AVPlayer.playerWithPlayerItem_(self.player_item)
        
        # Connect player to view
        self.view.setPlayer_(self.player)
        self.trimmer_view.setAsset_(self.player.currentItem().asset())
        output = AVPlayerItemVideoOutput.alloc().initWithPixelBufferAttributes_({
            kCVPixelBufferPixelFormatTypeKey: kCVPixelFormatType_32BGRA
        })
        self.player_item.addOutput_(output)
        self.output = output
        
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self,
            'playerItemDidReachEnd:',
            AVPlayerItemDidPlayToEndTimeNotification,
            self.player_item
        )

    def playerItemDidReachEnd_(self, notification):
        self.player.seekToTime_(CMTimeMake(0, 1))
        self.player.play()
    
    def play(self):
        self.player.play()
        self.is_playing = True
    
    def pause(self):
        self.player.pause()
        self.is_playing = False
    
    def get_current_frame(self):
        if not self.output:
            return None
            
        current_time = self.player_item.currentTime()
        
        if self.output.hasNewPixelBufferForItemTime_(current_time):
            pixel_buffer = self.output.copyPixelBufferForItemTime_itemTimeForDisplay_(current_time, None)
            if pixel_buffer:
                return self.convert_pixelbuffer_to_numpy(pixel_buffer)
        return None
    
    def convert_pixelbuffer_to_numpy(self, pixel_buffer):
        CVPixelBufferLockBaseAddress(pixel_buffer, 0)
        
        width = CVPixelBufferGetWidth(pixel_buffer)
        height = CVPixelBufferGetHeight(pixel_buffer)
        bytes_per_row = CVPixelBufferGetBytesPerRow(pixel_buffer)
        base_address = CVPixelBufferGetBaseAddress(pixel_buffer)
        
        buffer_data = buffer(base_address, bytes_per_row * height)
        arr = np.frombuffer(buffer_data, dtype=np.uint8)
        arr = arr.reshape((height, bytes_per_row // 4, 4))
        
        CVPixelBufferUnlockBaseAddress(pixel_buffer, 0)
        return arr

def main():
    if len(sys.argv) != 2:
        print("Usage: python script.py <video_path>")
        return
    
    # Initialize the application
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular) 
    
    video_path = sys.argv[1]
    player = VideoPlayer.alloc().init()
    player.load_video(video_path)
    player.play()
    
    try:
        # Run the application
        app.run()
    except KeyboardInterrupt:
        print("\nStopping playback...")
        player.pause()

if __name__ == "__main__":
    main()
