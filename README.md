# FIC Dashboard - Site Reliability Engineering Dashboard

A comprehensive SRE dashboard for monitoring and managing incidents with real-time job status tracking, automated API fetching, and multi-user session management.

## 🚀 Features

### Core Functionality
- **Real-time Incident Monitoring** - Live dashboard with 30-second API refresh
- **Multi-user Authentication** - Secure login with 8-hour session timeout
- **Incident Management** - Complete lifecycle tracking from detection to resolution
- **On-call Engineer Management** - L1/L2 engineer tracking with add/delete functionality
- **Priority Management** - P1-P4 incident prioritization with escalation/degradation
- **Weekly Statistics** - Comprehensive incident analytics and visualizations

### Technical Features
- **Async API Fetching** - Concurrent API calls with intelligent caching
- **Critical Incident Alerting** - Red flashing for unresponded critical/error jobs after 1 minute
- **Responsive Design** - Mobile-friendly interface with Tokyo Night color scheme
- **Database Persistence** - DuckDB for reliable data storage
- **Background Scheduling** - Automated job status monitoring

## 📋 Requirements

### System Requirements
- **Python 3.13+**
- **Node.js 18+** (for TailwindCSS)
- **pnpm** (package manager)

### Python Dependencies
- Flask 3.0+
- DuckDB 0.9+
- APScheduler 3.10+
- httpx 0.27+
- pytz 2024.1+
- python-dotenv 1.1+

### Frontend Dependencies
- TailwindCSS 3.4.1
- Chart.js (CDN)
- PostCSS & Autoprefixer

## 🛠️ Development Setup

### 1. Clone the Repository
```bash
git clone <repository-url>
cd fic-dashboard
```

### 2. Python Environment Setup
```bash
# Create virtual environment (recommended)
python -m venv .venv

# Activate virtual environment
# On macOS/Linux:
source .venv/bin/activate
# On Windows:
.venv\Scripts\activate

# Install Python dependencies
pip install -r requirements.txt
```

### 3. Frontend Dependencies Setup
```bash
# Install Node.js dependencies
pnpm install

# Build TailwindCSS (one-time)
pnpm run tailwind:build

# Or watch for changes during development
pnpm run tailwind:watch
```

### 4. Database Initialization
```bash
# Initialize the DuckDB database with default data
python database.py
```

### 5. Environment Configuration
Create a `.env` file in the project root:
```env
# Mock API URL (optional, defaults to localhost:5001)
MOCK_API_URL=http://localhost:5001/api/jobs

# Flask configuration
FLASK_ENV=development
FLASK_DEBUG=True
```

## 🚀 Running the Application

### Development Mode

#### Terminal 1: Start Mock API (for testing)
```bash
python mock_api.py
```
The mock API will run on `http://localhost:5001`

#### Terminal 2: Start TailwindCSS Watcher (optional)
```bash
pnpm run tailwind:watch
```

#### Terminal 3: Start Main Application
```bash
python app.py
```
The dashboard will be available at `http://localhost:5050`

### Default Login Credentials
- **Admin:** `admin` / `admin123`
- **Engineers:**
  - `alice.johnson` / `engineer123`
  - `bob.smith` / `engineer123`
  - `charlie.davis` / `engineer123`

## 📁 Project Structure

```
fic-dashboard/
├── app.py                 # Main Flask application
├── database.py           # Database schema and utilities
├── scheduler.py          # Background job scheduler
├── mock_api.py          # Mock API for testing
├── requirements.txt     # Python dependencies
├── package.json         # Node.js dependencies
├── tailwind.config.js   # TailwindCSS configuration
├── postcss.config.js    # PostCSS configuration
├── static/
│   ├── css/
│   │   ├── style.css    # TailwindCSS input
│   │   └── output.css   # Generated CSS (auto-generated)
│   └── js/
│       └── main.js      # Frontend JavaScript
├── templates/
│   ├── index.html       # Main dashboard template
│   └── login.html       # Login page template
└── .env                 # Environment variables (create this)
```

## 🔧 Configuration

### Database Configuration
The application uses DuckDB for data persistence. The database file `sre_dashboard.duckdb` is created automatically.

#### Database Schema
- **users** - User authentication and profiles
- **engineers** - On-call engineer management
- **incidents** - Incident tracking and lifecycle
- **app_state** - Application state (last refresh time, etc.)

### API Configuration
Configure external job monitoring APIs in the scheduler.py file:
```python
# Update the API endpoint in scheduler.py
API_ENDPOINT = "https://your-monitoring-api.com/jobs"
```

### Session Configuration
Session timeout and security settings in app.py:
```python
app.config["PERMANENT_SESSION_LIFETIME"] = datetime.timedelta(hours=8)
```

## 🎨 Customization

### Color Scheme
The application uses Tokyo Night color scheme. Modify `tailwind.config.js` to customize:
```javascript
colors: {
    'tn-bg': '#1a1b26',        // Background
    'tn-bg-alt': '#24283b',    // Alternative background
    'tn-text': '#a9b1d6',     // Primary text
    'tn-comment': '#565f89',   // Secondary text
    'tn-blue': '#7aa2f7',     // Blue accent
    'tn-purple': '#bb9af7',   // Purple accent
    'tn-cyan': '#7dcfff',     // Cyan accent
    'tn-green': '#9ece6a',    // Green accent
    'tn-yellow': '#e0af68',   // Yellow accent
    'tn-orange': '#ff9e64',   // Orange accent
    'tn-red': '#f7768e',      // Red accent
}
```

### Auto-refresh Interval
Modify the refresh interval in `scheduler.py`:
```python
# Change from 30 seconds to desired interval
scheduler.add_job(
    func=fetch_and_update_job_statuses,
    trigger="interval",
    seconds=30,  # Change this value
    args=[app]
)
```

## 🚀 Production Deployment

### 1. Environment Setup
```bash
# Set production environment
export FLASK_ENV=production
export FLASK_DEBUG=False

# Install production dependencies
pip install gunicorn
```

### 2. Build Assets
```bash
# Build optimized CSS
pnpm run tailwind:build
```

### 3. Database Setup
```bash
# Initialize production database
python database.py
```

### 4. Run with Gunicorn
```bash
# Basic production server
gunicorn -w 4 -b 0.0.0.0:5050 app:app

# With better configuration
gunicorn -w 4 -b 0.0.0.0:5050 --timeout 120 --keep-alive 2 app:app
```

### 5. Nginx Configuration (Optional)
```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:5050;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /static {
        alias /path/to/fic-dashboard/static;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
```

### 6. Docker Deployment (Optional)

#### Dockerfile
```dockerfile
FROM python:3.13-slim

WORKDIR /app

# Install Node.js for TailwindCSS
RUN apt-get update && apt-get install -y curl && \
    curl -fsSL https://deb.nodesource.com/setup_18.x | bash - && \
    apt-get install -y nodejs && \
    npm install -g pnpm

# Copy dependency files
COPY requirements.txt package.json pnpm-lock.yaml ./

# Install dependencies
RUN pip install -r requirements.txt && \
    pnpm install

# Copy application code
COPY . .

# Build CSS
RUN pnpm run tailwind:build

# Initialize database
RUN python database.py

# Expose port
EXPOSE 5050

# Run application
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5050", "app:app"]
```

#### docker-compose.yml
```yaml
version: '3.8'

services:
  fic-dashboard:
    build: .
    ports:
      - "5050:5050"
    environment:
      - FLASK_ENV=production
      - MOCK_API_URL=http://mock-api:5001/api/jobs
    volumes:
      - ./data:/app/data
    depends_on:
      - mock-api

  mock-api:
    build: .
    command: python mock_api.py
    ports:
      - "5001:5001"
    environment:
      - FLASK_ENV=production
```

#### Build and Run
```bash
# Build and start services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

## 🧪 Testing

### Manual Testing
1. **Authentication Testing**
   - Test login with valid/invalid credentials
   - Verify session timeout (8 hours)
   - Test logout functionality

2. **Incident Management Testing**
   - Create incidents via mock API
   - Test incident response workflow
   - Verify priority escalation/degradation
   - Test incident resolution

3. **Engineer Management Testing**
   - Add/delete engineers
   - Assign engineers to incidents
   - Test L1/L2 level management

### API Testing
```bash
# Test mock API endpoints
curl http://localhost:5001/api/jobs

# Test dashboard API endpoints (requires authentication)
curl -b cookies.txt http://localhost:5050/get-incident-count
```

## 🔍 Monitoring & Logging

### Application Logs
The application logs important events to the console. In production, configure proper logging:

```python
import logging

# Configure logging in app.py
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('fic-dashboard.log'),
        logging.StreamHandler()
    ]
)
```

### Health Checks
The application provides a debug endpoint for health monitoring:
```bash
curl http://localhost:5050/debug-status
```

### Database Monitoring
Monitor DuckDB file size and performance:
```bash
# Check database file size
ls -lh sre_dashboard.duckdb

# Monitor database connections (if needed)
```

## 🛡️ Security Considerations

### Production Security
1. **Change Default Passwords**
   ```python
   # Update default passwords in database.py
   default_users = [
       ("admin", hash_password("your-secure-password"), "System Administrator", "admin"),
       # ... update other default users
   ]
   ```

2. **Environment Variables**
   ```bash
   # Use environment variables for sensitive data
   export SECRET_KEY="your-secret-key"
   export DATABASE_URL="your-database-url"
   ```

3. **HTTPS Configuration**
   - Use SSL certificates in production
   - Configure secure headers
   - Enable CSRF protection if needed

4. **Session Security**
   - Configure secure session cookies
   - Use strong secret keys
   - Implement proper session invalidation

## 📚 API Documentation

### Authentication Endpoints
- `GET /login` - Login page
- `POST /login` - Authenticate user
- `GET /logout` - Logout user

### Dashboard Endpoints
- `GET /` - Main dashboard (requires auth)
- `POST /refresh-data` - Manual data refresh
- `GET /get-last-refresh-time` - Get last refresh time
- `GET /get-incident-count` - Get active incident count

### Incident Management
- `POST /respond-incident/<id>` - Respond to incident
- `POST /update-incident-priority/<id>` - Update priority
- `POST /update-inc-link/<id>` - Update incident link
- `POST /resolve-incident/<id>` - Resolve incident

### Engineer Management
- `POST /add-engineer` - Add new engineer
- `POST /delete-engineer/<id>` - Delete engineer

## 🤝 Contributing

### Development Workflow
1. Fork the repository
2. Create a feature branch
3. Make changes and test thoroughly
4. Update documentation if needed
5. Submit a pull request

### Code Style
- Follow PEP 8 for Python code
- Use meaningful variable names
- Add docstrings for functions
- Comment complex logic

### Testing Guidelines
- Test all new features manually
- Verify responsive design on different screen sizes
- Test authentication and session management
- Validate database operations

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🆘 Troubleshooting

### Common Issues

#### 1. Module Not Found Errors
```bash
# Ensure virtual environment is activated
source .venv/bin/activate  # macOS/Linux
.venv\Scripts\activate     # Windows

# Reinstall dependencies
pip install -r requirements.txt
```

#### 2. TailwindCSS Not Building
```bash
# Reinstall Node dependencies
rm -rf node_modules pnpm-lock.yaml
pnpm install
pnpm run tailwind:build
```

#### 3. Database Issues
```bash
# Recreate database
rm sre_dashboard.duckdb
python database.py
```

#### 4. Port Already in Use
```bash
# Find and kill process using port 5050
lsof -ti:5050 | xargs kill -9

# Or use different port
python app.py --port 5051
```

#### 5. Session Issues
- Clear browser cookies
- Check session timeout configuration
- Verify user exists in database

### Getting Help
- Check the logs for error messages
- Verify all dependencies are installed
- Ensure proper file permissions
- Test with default configuration first

---

**Happy monitoring! 🚀**
```