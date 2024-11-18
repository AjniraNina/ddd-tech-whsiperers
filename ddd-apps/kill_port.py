import os
import subprocess
import sys
import platform


def kill_port(port: int) -> bool:
    """
    Kill process running on specified port.
    Returns True if successful, False otherwise.
    """
    try:
        system = platform.system().lower()

        if system == "linux" or system == "darwin":  # Linux or Mac
            # Find PID using port
            cmd = f"lsof -i :{port} -t"
            pid = subprocess.check_output(cmd, shell=True).decode().strip()

            if pid:
                # Kill the process
                os.system(f"kill -9 {pid}")
                print(f"Successfully killed process {pid} on port {port}")
                return True

        elif system == "windows":
            # Find PID using netstat
            cmd = f"netstat -ano | findstr :{port}"
            result = subprocess.check_output(cmd, shell=True).decode()

            if result:
                # Extract PID from the last column
                pid = result.strip().split()[-1]
                # Kill the process
                os.system(f"taskkill /PID {pid} /F")
                print(f"Successfully killed process {pid} on port {port}")
                return True

        print(f"No process found running on port {port}")
        return False

    except subprocess.CalledProcessError:
        print(f"No process found running on port {port}")
        return False
    except Exception as e:
        print(f"Error killing process on port {port}: {str(e)}")
        return False


if __name__ == "__main__":
    port = 5000  # Default port

    # Allow port to be specified as command line argument
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            print("Please provide a valid port number")
            sys.exit(1)

    kill_port(port)
