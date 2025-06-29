import os
import subprocess
import tempfile
import shutil
from datetime import datetime
from pathlib import Path
from driver import LOCAL_DIR, OUTPUT_DIR

class VideoProcessor:
    def __init__(self, video_chunks_dir, audio_chunks_dir, video_output_dir, audio_output_dir, transcript_output_dir=None):
        self.video_chunks_dir = Path(video_chunks_dir)
        self.audio_chunks_dir = Path(audio_chunks_dir)
        self.video_output_dir = Path(video_output_dir)
        self.audio_output_dir = Path(audio_output_dir)
        self.transcript_output_dir = Path(transcript_output_dir) if transcript_output_dir else None
        
        # Create output directories
        self.video_output_dir.mkdir(parents=True, exist_ok=True)
        self.audio_output_dir.mkdir(parents=True, exist_ok=True)
        if self.transcript_output_dir:
            self.transcript_output_dir.mkdir(parents=True, exist_ok=True)
        
    def run_ffmpeg(self, command, description="FFmpeg operation"):
        """Execute FFmpeg command with proper error handling"""
        print(f"[ffmpeg] {description}")
        print(f"[ffmpeg] Running: {' '.join(command)}")
        
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=True
            )
            return True, result.stdout
        except subprocess.CalledProcessError as e:
            print(f"[ffmpeg] Error: {e}")
            if e.stderr:
                print(f"[ffmpeg] stderr: {e.stderr}")
            return False, e.stderr
    
    def find_chunk_sequences(self):
        """Find video and audio chunk sequences"""
        video_chunks = []
        audio_chunks = []
        
        # Find all video chunks in video directory
        for i in range(1000):  # reasonable upper bound
            video_file = self.video_chunks_dir / f"video_{i}.webm"
            if video_file.exists():
                video_chunks.append((i, video_file))
        
        # Find all audio chunks in audio directory
        for i in range(1000):  # reasonable upper bound
            audio_file = self.audio_chunks_dir / f"audio_{i}.webm"
            if audio_file.exists():
                audio_chunks.append((i, audio_file))
        
        # Sort by index
        video_chunks.sort(key=lambda x: x[0])
        audio_chunks.sort(key=lambda x: x[0])
        
        print(f"📊 Found {len(video_chunks)} video chunks and {len(audio_chunks)} audio chunks")
        
        return video_chunks, audio_chunks
    
    def concatenate_raw_chunks(self, chunks, output_path, chunk_type="video"):
        """Concatenate raw WebM chunks using binary concatenation first, then fix with FFmpeg"""
        if not chunks:
            return False
            
        print(f"🔗 Concatenating {len(chunks)} {chunk_type} chunks...")
        
        # First, try binary concatenation to a temporary file
        temp_concat_path = output_path.with_suffix('.temp.webm')
        
        try:
            with open(temp_concat_path, 'wb') as outfile:
                for i, (index, chunk_path) in enumerate(chunks):
                    print(f"   Adding {chunk_type}_{index}.webm")
                    with open(chunk_path, 'rb') as infile:
                        outfile.write(infile.read())
            
            print(f"📦 Raw concatenation complete, fixing with FFmpeg...")
            
            # Now use FFmpeg to fix the concatenated file
            command = [
                "ffmpeg",
                "-y",
                "-fflags", "+genpts",  # Generate PTS for frames
                "-i", str(temp_concat_path),
                "-c", "copy",  # Copy without re-encoding
                "-avoid_negative_ts", "make_zero",
                str(output_path)
            ]
            
            success, output = self.run_ffmpeg(
                command,
                f"Fixing concatenated {chunk_type} file"
            )
            
            # Clean up temp file
            if temp_concat_path.exists():
                temp_concat_path.unlink()
            
            return success
            
        except Exception as e:
            print(f"❌ Error during raw concatenation: {e}")
            if temp_concat_path.exists():
                temp_concat_path.unlink()
            return False
    
    def mux_video_audio_with_captions(self, video_path, audio_path, srt_path, output_path):
        """Mux video and audio streams together with soft captions into MP4"""
        # Build command based on available inputs
        command = ["ffmpeg", "-y"]
        
        # Add video input
        command.extend(["-i", str(video_path)])
        
        # Add audio input if available
        if audio_path:
            command.extend(["-i", str(audio_path)])
        
        # Video codec - use h264 for better compatibility
        command.extend(["-c:v", "libx264", "-preset", "medium", "-crf", "23"])
        
        # Audio codec if audio is available
        if audio_path:
            command.extend(["-c:a", "aac", "-b:a", "128k"])
        
        # Add subtitle if available
        if srt_path and srt_path.exists():
            command.extend(["-i", str(srt_path), "-c:s", "mov_text", "-metadata:s:s:0", "language=eng"])
        
        # Other options
        command.extend(["-shortest", "-avoid_negative_ts", "make_zero"])
        
        # Output file
        command.append(str(output_path))
        
        success, output = self.run_ffmpeg(
            command,
            "Creating MP4 with video, audio, and soft captions"
        )
        
        return success

    def process_chunks(self, srt_path=None):
        """Main processing function"""
        print("🔍 Scanning for chunk sequences...")
        
        # Find all chunks
        video_chunks, audio_chunks = self.find_chunk_sequences()
        
        if not video_chunks:
            print("❌ No video chunks found!")
            return None, None
        
        if not audio_chunks:
            print("⚠️  No audio chunks found, will create video-only output")
        
        # Create temporary directory for processing
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir = Path(temp_dir)
            print(f"📁 Using temporary directory: {temp_dir}")
            
            # Step 1: Concatenate video chunks
            print("\n🎬 Processing video chunks...")
            video_concat_path = temp_dir / "video_concatenated.webm"
            
            if not self.concatenate_raw_chunks(video_chunks, video_concat_path, "video"):
                print("❌ Failed to concatenate video chunks!")
                return None, None
            
            # Step 2: Concatenate audio chunks (if they exist)
            audio_concat_path = None
            if audio_chunks:
                print("\n🎵 Processing audio chunks...")
                audio_concat_path = temp_dir / "audio_concatenated.webm"
                
                if not self.concatenate_raw_chunks(audio_chunks, audio_concat_path, "audio"):
                    print("⚠️  Failed to concatenate audio chunks, proceeding with video only")
                    audio_concat_path = None
            
            # Step 3: Generate output filenames with timestamp
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            final_video_path = self.video_output_dir / f"output_{timestamp}.mp4"  # Changed to MP4
            final_audio_path = self.audio_output_dir / f"audio_{timestamp}.wav"
            
            # Step 4: Convert and save audio as WAV if available
            saved_audio_path = None
            if audio_concat_path and audio_concat_path.exists():
                print(f"\n🎵 Converting audio to WAV format: {final_audio_path}")
                try:
                    # Convert WebM to WAV using FFmpeg
                    command = [
                        "ffmpeg",
                        "-y",
                        "-i", str(audio_concat_path),
                        "-acodec", "pcm_s16le",  # Standard WAV format
                        "-ar", "44100",          # Sample rate
                        str(final_audio_path)
                    ]
                    
                    success, output = self.run_ffmpeg(
                        command,
                        "Converting audio to WAV format"
                    )
                    
                    if success:
                        saved_audio_path = final_audio_path
                        print(f"✅ Audio file converted and saved as WAV successfully!")
                    else:
                        print(f"❌ Failed to convert audio to WAV format")
                except Exception as e:
                    print(f"❌ Failed to save audio as WAV: {e}")
            
            # Step 5: Create final MP4 video with soft captions
            print(f"\n🎞️  Creating final MP4 with captions: {final_video_path}")
            
            if self.mux_video_audio_with_captions(video_concat_path, audio_concat_path, srt_path, final_video_path):
                print(f"✅ Success! Final MP4 video saved at:")
                print(f"   {final_video_path}")
                if srt_path and srt_path.exists():
                    print(f"   With embedded soft captions from: {srt_path}")
                return final_video_path, saved_audio_path
            else:
                print("❌ Failed to create final MP4 video!")
                return None, saved_audio_path

def main():
    start_time = datetime.now() # Start timing

    # Configuration - Updated paths
    video_chunks_dir = os.path.join(LOCAL_DIR, "video")
    audio_chunks_dir = os.path.join(LOCAL_DIR, "audio")
    video_output_dir = os.path.join(OUTPUT_DIR, "video")
    audio_output_dir = os.path.join(OUTPUT_DIR, "audio")
    transcript_output_dir = os.path.join(OUTPUT_DIR, "transcripts")
    
    # Check if FFmpeg is available
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        print("✅ FFmpeg found and ready")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("❌ FFmpeg not found! Please install FFmpeg and ensure it's in your PATH")
        return
    
    # Check if input directories exist
    if not os.path.exists(video_chunks_dir):
        print(f"❌ Video chunks directory not found: {video_chunks_dir}")
        return
    
    if not os.path.exists(audio_chunks_dir):
        print(f"⚠️  Audio chunks directory not found: {audio_chunks_dir}")
        print("   Proceeding with video-only processing...")
    
    print(f"📂 Video chunks directory: {video_chunks_dir}")
    print(f"📂 Audio chunks directory: {audio_chunks_dir}")
    print(f"📂 Video output directory: {video_output_dir}")
    print(f"📂 Audio output directory: {audio_output_dir}")
    print(f"📂 Transcript output directory: {transcript_output_dir}")
    
    # No SRT file available when running standalone
    srt_path = None
    
    # Process chunks
    processor = VideoProcessor(
        video_chunks_dir, 
        audio_chunks_dir, 
        video_output_dir, 
        audio_output_dir,
        transcript_output_dir
    )
    video_result, audio_result = processor.process_chunks(srt_path)
    
    if video_result:
        print(f"\n🎉 Processing completed successfully!")
        print(f"📁 Video file: {video_result}")
        
        if audio_result:
            print(f"📁 Audio file: {audio_result}")
        
        # Show file info
        try:
            video_size = video_result.stat().st_size / (1024 * 1024)  # MB
            print(f"📊 Video file size: {video_size:.2f} MB")
            
            if audio_result:
                audio_size = audio_result.stat().st_size / (1024 * 1024)  # MB
                print(f"📊 Audio file size: {audio_size:.2f} MB")
        except:
            pass
    else:
        print(f"\n💥 Processing failed!")
    
    end_time = datetime.now()  # End timing
    duration = end_time - start_time
    print(f"\n⏱️ Total processing time: {duration}")

if __name__ == "__main__":
    main()