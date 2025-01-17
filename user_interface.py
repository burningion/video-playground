import av
from imgui_bundle import imgui, hello_imgui
import OpenGL.GL as gl
import numpy as np
import subprocess
import json
import sounddevice as sd
import threading
import queue
import time

def check_side_data_ffprobe(filename):
    cmd = [
        'ffprobe',
        '-v', 'quiet',
        '-print_format', 'json',
        '-show_streams',  # Changed from -show_frames to -show_streams
        '-select_streams', 'v:0',
        filename
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    
    # Check streams for side_data_list
    streams = data.get('streams', [])
    for stream in streams:
        if 'side_data_list' in stream:
            return {
                'has_side_data': True,
                'side_data': stream['side_data_list']
            }
    
    return {
        'has_side_data': False,
        'side_data': None
    }

class VideoPlayer:
    def __init__(self, video_path):
        self.video_path = video_path
        side = check_side_data_ffprobe(video_path)
        self.container = av.open(video_path)
        self.stream = self.container.streams.video[0]
        
        # Initialize audio components
        self.audio_stream = None
        self.audio_queue = queue.Queue(maxsize=100)  # Increased buffer size further
        self.is_playing = False
        self.audio_device = None
        
        # Frame timing control
        self.frame_rate = float(self.stream.guessed_rate or self.stream.rate or 30)
        self.frame_interval = 1.0 / self.frame_rate
        self.last_frame_time = 0
        
        # Frame buffer
        self.current_frame = None
        self.next_frame = None
        self.next_frame_pts = None
        self.frame_ready = False
        
        # Try to get audio stream
        audio_streams = [s for s in self.container.streams if s.type == 'audio']
        if audio_streams:
            self.audio_stream = audio_streams[0]
            self.audio_sample_rate = self.audio_stream.rate
            self.audio_channels = self.audio_stream.channels
            print(f"Audio: {self.audio_channels} channels @ {self.audio_sample_rate}Hz")
            
        self.duration = float(self.stream.duration * self.stream.time_base)
        self.current_time = 0.0
        self.texture_id = gl.glGenTextures(1)
        self.video_path = video_path
        self.original_width = self.stream.width
        self.original_height = self.stream.height
        # Initialize video dimensions and rotation
        self._init_video_dimensions()
        self.rotation = 0
        if side['has_side_data']:
            try:
                for side_data in side['side_data']:
                    if 'rotation' in side_data:
                        self.rotation = side_data['rotation']
            except Exception as e:
                print(f"Error reading side data: {e}")
                
        if self.rotation in [90, 270, -90]:
            self.frame_width = self.original_height
            self.frame_height = self.original_width
        else:
            self.frame_width = self.original_width
            self.frame_height = self.original_height
            
        # Get first frame
        for frame in self.container.decode(video=0):
            self.current_frame = frame.to_ndarray(format='rgb24')
            self.frame_width = frame.width
            self.frame_height = frame.height
            break
            
        self._update_texture()
        
    def seek_frame(self, timestamp):
        try:
            # Allow seeking all the way to the end, but be extra careful
            timestamp = max(0, min(timestamp, self.duration))
            is_seeking_end = timestamp >= self.duration - 0.1  # Flag for end-seeking
            
            # Reopen the container
            self.container = av.open(self.video_path)
            self.stream = self.container.streams.video[0]
            
            # If we're seeking to the end, use a special approach
            if is_seeking_end:
                # Seek close to the end first
                self.container.seek(int((self.duration - 1.0) / self.stream.time_base), stream=self.stream)
                # Keep the last frame we see
                last_frame = None
                for frame in self.container.decode(video=0):
                    last_frame = frame
                if last_frame:
                    self.current_frame = last_frame.to_ndarray(format='rgb24')
                    self._update_texture()
                    self.current_time = self.duration
                return
                
            # Normal seeking for all other cases
            seek_pts = int(timestamp / self.stream.time_base)
            self.container.seek(seek_pts, stream=self.stream)
            
            # Decode frames until we get the exact frame we want
            for frame in self.container.decode(video=0):
                frame_ts = float(frame.pts * self.stream.time_base)
                if abs(frame_ts - timestamp) < self.stream.time_base:
                    self.current_frame = frame.to_ndarray(format='rgb24')
                    self._update_texture()
                    self.current_time = frame_ts
                    break
                elif frame_ts > timestamp:
                    self.current_frame = frame.to_ndarray(format='rgb24')
                    self._update_texture()
                    self.current_time = frame_ts
                    break
                
        except Exception as e:
            print(f"Seek error: {e}")

    def _init_video_dimensions(self):
        self.original_width = self.stream.width
        self.original_height = self.stream.height
        self.rotation = self._get_rotation()
        
        if self.rotation in [90, 270, -90]:
            self.frame_width = self.original_height
            self.frame_height = self.original_width
        else:
            self.frame_width = self.original_width
            self.frame_height = self.original_height

    def _get_rotation(self):
        try:
            for s in self.stream.side_data:
                if isinstance(s, av.VideoDisplayMatrix):
                    return s.rotation
        except Exception as e:
            print(f"Error getting rotation: {e}")
        return 0

    def _audio_decode_thread(self):
        """Dedicated thread for audio decoding"""
        try:
            # Open a separate container for audio
            audio_container = av.open(self.video_path)
            audio_stream = audio_container.streams.audio[0]
            audio_stream.thread_type = 'AUTO'
            
            resampler = av.AudioResampler(
                format=av.AudioFormat('s16').packed,
                layout='stereo' if self.audio_channels == 2 else 'mono',
                rate=self.audio_sample_rate
            )
            
            for frame in audio_container.decode(audio=0):
                if not self.is_playing:
                    break
                    
                try:
                    # Resample audio frame
                    frame = resampler.resample(frame)[0]
                    
                    # Convert to numpy array
                    audio_data = frame.to_ndarray()
                    
                    # Ensure correct shape (samples, channels)
                    if audio_data.ndim == 1:
                        audio_data = audio_data.reshape(-1, 1)
                    elif audio_data.ndim == 2 and audio_data.shape[0] < audio_data.shape[1]:
                        audio_data = audio_data.T
                    
                    # Convert to float32 for sounddevice
                    audio_data = audio_data.astype(np.float32) / 32768.0
                    
                    # Handle queue full condition
                    try:
                        self.audio_queue.put(audio_data, timeout=1.0)
                    except queue.Full:
                        # If queue is full, remove oldest item and try again
                        try:
                            self.audio_queue.get_nowait()
                            self.audio_queue.put(audio_data)
                        except (queue.Empty, queue.Full):
                            continue
                            
                except Exception as e:
                    print(f"Audio processing error: {e}")
                    continue
                    
            audio_container.close()
            
        except Exception as e:
            print(f"Audio decode thread error: {e}")
            self.is_playing = False

    def _video_decode_thread(self):
        """Dedicated thread for video decoding"""
        try:
            video_stream = self.container.streams.video[0]
            stream_time_base = float(video_stream.time_base)
            
            for frame in self.container.decode(video=0):
                if not self.is_playing:
                    break
                    
                while self.frame_ready and self.is_playing:
                    time.sleep(0.001)
                    
                if not self.is_playing:
                    break
                    
                self.next_frame = frame.to_ndarray(format='rgb24')
                self.next_frame_pts = float(frame.pts * stream_time_base)
                self.frame_ready = True
                
        except Exception as e:
            print(f"Video decode thread error: {e}")
            self.is_playing = False

    def _audio_callback(self, outdata, frames, time_info, status):
        """Callback for audio output"""
        try:
            if status:
                print(f"Audio status: {status}")
                
            if not self.is_playing:
                outdata.fill(0)
                return
                
            # Try to get data with a small timeout
            try:
                data = self.audio_queue.get(timeout=0.1)
            except queue.Empty:
                outdata.fill(0)
                return
                
            # Handle output size
            if len(data) < len(outdata):
                outdata[:len(data)] = data
                outdata[len(data):].fill(0)
            else:
                outdata[:] = data[:len(outdata)]
                
        except Exception as e:
            print(f"Audio callback error: {e}")
            outdata.fill(0)
    def play(self):
        """Start video playback"""
        try:
            if not self.is_playing:
                print("Starting playback...")
                self.is_playing = True
                
                # Start audio if available
                if self.audio_stream:
                    try:
                        print("Starting audio stream...")
                        self.audio_device = sd.OutputStream(
                            channels=self.audio_channels,
                            samplerate=self.audio_sample_rate,
                            callback=self._audio_callback,
                            blocksize=1024,
                            latency='low'
                        )
                        self.audio_device.start()
                        
                        # Start audio decode thread
                        self.audio_thread = threading.Thread(target=self._audio_decode_thread)
                        self.audio_thread.daemon = True
                        self.audio_thread.start()
                        
                    except Exception as e:
                        print(f"Audio start error: {e}")
                        self.audio_stream = None
                
                # Reset frame state
                self.frame_ready = False
                self.next_frame = None
                self.next_frame_pts = None
                self.last_frame_time = time.time()
                
                # Start video decode thread
                self.video_thread = threading.Thread(target=self._video_decode_thread)
                self.video_thread.daemon = True
                self.video_thread.start()
                
        except Exception as e:
            print(f"Play error: {e}")
            self.is_playing = False

            
    def _update_texture(self):
        try:
            if self.rotation:
                k = {90: 1, 180: 2, 270: 3, -90: 3, -180: 2, -270: 1}.get(self.rotation, 0)
                if k:
                    self.current_frame = np.ascontiguousarray(np.rot90(self.current_frame, k=k))
                    
            gl.glBindTexture(gl.GL_TEXTURE_2D, self.texture_id)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
            gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 1)
            
            actual_height, actual_width = self.current_frame.shape[:2]
            gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGB, 
                           actual_width, actual_height,
                           0, gl.GL_RGB, gl.GL_UNSIGNED_BYTE, self.current_frame)
        except Exception as e:
            print(f"Texture update error: {e}")
            
    def pause(self):
        """Pause video playback"""
        self.is_playing = False
        
        # Stop audio
        if self.audio_device:
            try:
                self.audio_device.stop()
                self.audio_device.close()
                self.audio_device = None
            except Exception as e:
                print(f"Error stopping audio: {e}")
        
        # Clear audio queue
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break
                
    def cleanup(self):
        """Clean up resources"""
        self.is_playing = False
        if hasattr(self, 'container'):
            self.container.close()
        if hasattr(self, 'texture_id'):
            try:
                gl.glDeleteTextures(self.texture_id)
            except Exception as e:
                print(f"Cleanup error: {e}")
                
    def render_gui(self):
        try:
            current_time = time.time()
            
            # Check if it's time to display the next frame
            if self.is_playing and self.frame_ready and (current_time - self.last_frame_time) >= self.frame_interval:
                self.current_frame = self.next_frame
                self.current_time = self.next_frame_pts
                self.frame_ready = False
                self.last_frame_time = current_time
                self._update_texture()
            
            viewport = imgui.get_main_viewport()
            imgui.set_next_window_pos(viewport.pos)
            imgui.set_next_window_size(viewport.size)
            
            window_flags = (
                imgui.WindowFlags_.no_decoration |
                imgui.WindowFlags_.no_move |
                imgui.WindowFlags_.no_background |
                imgui.WindowFlags_.no_bring_to_front_on_focus |
                imgui.WindowFlags_.no_nav_focus |
                imgui.WindowFlags_.no_saved_settings
            )
            
            try:
                imgui.begin("Video Window", flags=window_flags)
                
                # Video display
                avail_width = imgui.get_content_region_avail().x
                avail_height = imgui.get_content_region_avail().y - 60
                
                if self.rotation in [90, -90, 270, -270]:
                    aspect_ratio = self.frame_height / self.frame_width
                else:
                    aspect_ratio = self.frame_width / self.frame_height
                    
                if avail_width / avail_height > aspect_ratio:
                    display_height = avail_height
                    display_width = avail_height * aspect_ratio
                else:
                    display_width = avail_width
                    display_height = avail_width / aspect_ratio
                
                imgui.set_cursor_pos_x((avail_width - display_width) * 0.5)
                imgui.image(self.texture_id, imgui.ImVec2(display_width, display_height))
                
                imgui.spacing()
                imgui.spacing()
                
                # Controls
                controls_width = min(avail_width * 0.8, 600)
                imgui.set_cursor_pos_x((avail_width - controls_width) * 0.5)
                
                if imgui.button("Play" if not self.is_playing else "Pause"):
                    if self.is_playing:
                        self.pause()
                    else:
                        self.play()
                        
                imgui.same_line()
                
                # Time slider
                imgui.push_item_width(controls_width - 100)
                changed, value = imgui.slider_float(
                    "##time",
                    self.current_time,
                    0,
                    self.duration,
                    "%.2f s"
                )
                if changed:
                    self.pause()
                    self.seek_frame(value)
                    
                imgui.pop_item_width()
                imgui.end()
                
            except Exception as e:
                print(f"ImGui error: {e}")
                if imgui.get_current_window() is not None:
                    imgui.end()
                
        except Exception as e:
            print(f"Render error: {e}")
            if imgui.get_current_window() is not None:
                imgui.end()

def main():
    player = None
    import sys
    video_file = sys.argv[1]
    def gui_setup():
        nonlocal player
        player = VideoPlayer(video_file)
        imgui.style_colors_dark()
        style = imgui.get_style()
        style.window_padding = imgui.ImVec2(0, 0)
        style.window_rounding = 0
        style.window_border_size = 0
    
    def gui_frame():
        if player:
            player.render_gui()
    
    def before_exit():
        nonlocal player
        if player:
            player.cleanup()
    
    runner_params = hello_imgui.RunnerParams()
    runner_params.app_window_params.window_title = "Video Player"
    runner_params.app_window_params.window_geometry.size = (1280, 720)
    runner_params.app_window_params.restore_previous_geometry = True
    
    runner_params.imgui_window_params.default_imgui_window_type = (
        hello_imgui.DefaultImGuiWindowType.no_default_window
    )
    
    runner_params.callbacks.show_gui = gui_frame
    runner_params.callbacks.post_init = gui_setup
    runner_params.callbacks.before_exit = before_exit
    
    hello_imgui.run(runner_params)

if __name__ == "__main__":
    main()