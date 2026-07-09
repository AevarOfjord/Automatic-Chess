import time
from game_manager import GameManager

# Monkey-patch time.sleep to run the simulation extremely fast
original_sleep = time.sleep
time.sleep = lambda x: original_sleep(0.001)

def run_test():
    print("Starting automated simulation test...")
    manager = GameManager(use_dummy_engine=False)
    try:
        manager.play_game()
        print("Simulation test completed successfully!")
    except Exception as e:
        print(f"Simulation failed with error: {e}")

if __name__ == "__main__":
    run_test()
