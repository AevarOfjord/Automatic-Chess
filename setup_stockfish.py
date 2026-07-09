import urllib.request
import zipfile
import os

STOCKFISH_URL = "https://github.com/official-stockfish/Stockfish/releases/latest/download/stockfish-windows-x86-64-avx2.zip"
ZIP_PATH = "stockfish.zip"
EXTRACT_PATH = "stockfish_dir"

def setup_stockfish():
    print(f"Downloading Stockfish from {STOCKFISH_URL}...")
    try:
        urllib.request.urlretrieve(STOCKFISH_URL, ZIP_PATH)
        print("Download complete. Extracting...")
        
        with zipfile.ZipFile(ZIP_PATH, 'r') as zip_ref:
            zip_ref.extractall(EXTRACT_PATH)
            
        print("Extraction complete.")
        
        # Find the executable
        exe_path = None
        for root, dirs, files in os.walk(EXTRACT_PATH):
            for file in files:
                if file.endswith(".exe"):
                    exe_path = os.path.join(root, file)
                    break
                    
        if exe_path:
            print(f"Stockfish ready! Executable found at: {exe_path}")
            # Optional: Move to root and clean up
            os.rename(exe_path, "stockfish.exe")
            print("Moved executable to project root as 'stockfish.exe'")
            
            # Cleanup
            os.remove(ZIP_PATH)
            import shutil
            shutil.rmtree(EXTRACT_PATH)
            print("Cleanup complete.")
        else:
            print("Could not find Stockfish executable in the zip file.")
            
    except Exception as e:
        print(f"Error setting up stockfish: {e}")

if __name__ == "__main__":
    setup_stockfish()
