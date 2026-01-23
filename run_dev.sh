#!/bin/bash
# MeloMatch Development Server Runner
# Runs Django, Celery worker, and ensures Redis is running

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}   MeloMatch Development Server${NC}"
echo -e "${BLUE}========================================${NC}"

# Cleanup function to kill all background processes on exit
cleanup() {
    echo -e "\n${YELLOW}Shutting down services...${NC}"
    
    # Kill Django server
    if [ ! -z "$DJANGO_PID" ]; then
        kill $DJANGO_PID 2>/dev/null && echo -e "${GREEN}✓ Django server stopped${NC}"
    fi
    
    # Kill Celery worker
    if [ ! -z "$CELERY_PID" ]; then
        kill $CELERY_PID 2>/dev/null && echo -e "${GREEN}✓ Celery worker stopped${NC}"
    fi
    
    echo -e "${GREEN}All services stopped. Goodbye!${NC}"
    exit 0
}

# Set up trap to catch Ctrl+C and other termination signals
trap cleanup SIGINT SIGTERM

# Activate virtual environment
if [ -d ".venv" ]; then
    echo -e "${YELLOW}Activating virtual environment...${NC}"
    source .venv/bin/activate
else
    echo -e "${RED}Virtual environment not found! Run 'uv venv' first.${NC}"
    exit 1
fi

# Check and start Redis
echo -e "${YELLOW}Checking Redis...${NC}"
if command -v docker &> /dev/null; then
    # Check if Redis container exists
    if docker ps -a --format '{{.Names}}' | grep -q 'melomatch-redis'; then
        # Container exists, check if running
        if ! docker ps --format '{{.Names}}' | grep -q 'melomatch-redis'; then
            echo -e "${YELLOW}Starting Redis container...${NC}"
            docker start melomatch-redis
        fi
        echo -e "${GREEN}✓ Redis container running${NC}"
    else
        # Create and start new Redis container
        echo -e "${YELLOW}Creating Redis container...${NC}"
        docker run -d --name melomatch-redis -p 6379:6379 redis:alpine
        echo -e "${GREEN}✓ Redis container created and running${NC}"
    fi
elif redis-cli ping &> /dev/null; then
    echo -e "${GREEN}✓ Redis already running${NC}"
else
    echo -e "${RED}Redis not found! Please install Redis or Docker.${NC}"
    echo -e "${YELLOW}Install with: brew install redis${NC}"
    echo -e "${YELLOW}Or use Docker: docker run -d --name melomatch-redis -p 6379:6379 redis:alpine${NC}"
    exit 1
fi

# Wait for Redis to be ready
echo -e "${YELLOW}Waiting for Redis to be ready...${NC}"
for i in {1..10}; do
    # Try redis-cli first, then fall back to docker exec
    if redis-cli ping &> /dev/null 2>&1; then
        echo -e "${GREEN}✓ Redis is ready${NC}"
        break
    elif docker exec melomatch-redis redis-cli ping &> /dev/null 2>&1; then
        echo -e "${GREEN}✓ Redis is ready (via Docker)${NC}"
        break
    fi
    if [ $i -eq 10 ]; then
        echo -e "${YELLOW}⚠ Could not verify Redis, continuing anyway...${NC}"
    fi
    sleep 1
done

# Start Celery worker in background
echo -e "${YELLOW}Starting Celery worker...${NC}"
uv run celery -A MeloMatch worker -l INFO --concurrency=4 &> /tmp/celery_melomatch.log &
CELERY_PID=$!
sleep 2

if ps -p $CELERY_PID > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Celery worker started (PID: $CELERY_PID)${NC}"
    echo -e "${BLUE}  Logs: /tmp/celery_melomatch.log${NC}"
else
    echo -e "${RED}Celery worker failed to start!${NC}"
    cat /tmp/celery_melomatch.log
    exit 1
fi

# Start Django development server
echo -e "${YELLOW}Starting Django server...${NC}"
uv run python manage.py runserver 0.0.0.0:8000 &> /tmp/django_melomatch.log &
DJANGO_PID=$!
sleep 2

if ps -p $DJANGO_PID > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Django server started (PID: $DJANGO_PID)${NC}"
    echo -e "${BLUE}  Logs: /tmp/django_melomatch.log${NC}"
else
    echo -e "${RED}Django server failed to start!${NC}"
    cat /tmp/django_melomatch.log
    cleanup
    exit 1
fi

echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}   All services running!${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "${BLUE}Django:${NC}  http://localhost:8000"
echo -e "${BLUE}API:${NC}    http://localhost:8000/api/"
echo -e "${BLUE}Admin:${NC}  http://localhost:8000/admin/"
echo -e "${BLUE}Redis:${NC}  localhost:6379"
echo -e ""
echo -e "${YELLOW}Press Ctrl+C to stop all services${NC}"
echo -e ""

# Tail both log files
echo -e "${BLUE}--- Live Logs (Django & Celery) ---${NC}"
tail -f /tmp/django_melomatch.log /tmp/celery_melomatch.log 2>/dev/null &
TAIL_PID=$!

# Wait for any background process to exit
wait $DJANGO_PID $CELERY_PID 2>/dev/null

# If we get here, something crashed
cleanup
