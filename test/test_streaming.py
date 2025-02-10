import asyncio
import websockets
import json
import os
import shutil
import subprocess
import time

  
def linstt_streaming(*kargs, **kwargs):
    text = asyncio.run(_linstt_streaming(*kargs, **kwargs))
    return text

async def _linstt_streaming(
    audio_file,
    ws_api = "ws://localhost:8080/streaming",
    verbose = False,
    language = None
):
    
    if audio_file is None:
        import pyaudio
        # Init pyaudio
        audio = pyaudio.PyAudio()
        stream = audio.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True, frames_per_buffer=2048)
        if verbose > 1:
            print("Start recording")
    else:
        process = subprocess.run(["ffmpeg", "-y", "-i", audio_file, "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", "tmp.wav"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stream = open("tmp.wav", "rb")
    text = ""
    partial = None
        # Check if the process completed successfully
    if process.returncode == 0:
        print("FFmpeg conversion successful.")
    else:
        print("FFmpeg conversion failed:", process.stderr.decode())
    async with websockets.connect(ws_api) as websocket:
        if language is not None:
            config = {"config" : {"sample_rate": 16000, "language": language}}
        else: 
            config = {"config" : {"sample_rate": 16000}}
        await websocket.send(json.dumps(config))
        while True:
            data = stream.read(2*2*16000)
            if audio_file and not data:
                if verbose > 1:
                    print("\nAudio file finished")
                break
            await websocket.send(data)
            res = await websocket.recv()
            message = json.loads(res)
            if message is None:
                if verbose > 1:
                    print("\n Received None")
                continue
            if "partial" in message.keys():
                partial = message["partial"]
                if partial and verbose:
                    print_partial(partial)
            elif "text" in message.keys():
                line = message["text"]
                if line and verbose:
                    print_final(line)
                if line:
                    if text:
                        text += "\n"
                    text += line
            elif verbose:
                print(f"??? {message}")
        if verbose > 1:
            print("Sending EOF")
        await websocket.send('{"eof" : 1}')
        res = await websocket.recv()
        message = json.loads(res)
        if isinstance(message, str):
            message = json.loads(message)
        if verbose > 1:
            print("Received EOF", message)
        if text:
            text += " "
        text += message["text"]
    if verbose:
        print_final("= FULL TRANSCRIPTION ", background="=")
        print(text)
    if audio_file is not None:
        stream.close()
        os.remove("tmp.wav")

    return text
        
def print_partial(text):
    text = text + "…"
    terminal_size = shutil.get_terminal_size()
    width = terminal_size.columns
    start = ((len(text) - 1)// width) * width
    if start > 0:
        print(" "*width, end="\r")
        if start < len(text) - 1:
            print("…"+text[start+1:]+" "*(width-len(text)-start-1), end="\r")
        else:
            print(text[-width:], end="\r")
    else:
        print(text, end="\r")

def print_final(text, background=" "):
    terminal_size = shutil.get_terminal_size()
    width = terminal_size.columns
    print(background * width, end="\r")
    print(text)
    
if __name__ == "__main__":

    import argparse
    parser = argparse.ArgumentParser(description='Transcribe input streaming (from mic or a file) with LinSTT',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument('--server', help='Transcription server',
        default="ws://localhost:8080/streaming",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose mode")
    parser.add_argument("--audio_file", default=None, help="A path to an audio file to transcribe (if not provided, use mic)")
    parser.add_argument("--language", default=None, help="Language model to use")
    args = parser.parse_args()
    print("filename = ", args.audio_file)
    res = linstt_streaming(args.audio_file, args.server, verbose=2 if args.verbose else 1, language=args.language)