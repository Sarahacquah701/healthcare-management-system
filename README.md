# Hospital Queue and Appointment Management System

This is a web-based application for managing hospital appointments and queues, built with Flask.

## Features

- User registration and login with roles: Patient, Doctor, Admin
- Patients can book appointments and join walk-in queues
- Doctors can manage their consultation queues
- Admins can add doctors and view users
- Real-time queue position and estimated waiting time
- Responsive design with Bootstrap

## Setup

1. Ensure Python 3.7+ is installed.
2. (Optional) Create a virtual environment in the project folder:
   - `python -m venv .venv`
3. Activate the virtual environment:
   - `.\.venv\Scripts\activate`
4. Install dependencies: `pip install -r requirements.txt`
5. Run the application:
   - `python app.py`
6. Open http://127.0.0.1:5000 in your browser.

## Default Admin

- Username: admin
- Password: admin123

## Usage

- Register as a patient or doctor.
- Patients: Book appointments or join queues.
- Doctors: View and manage queues.
- Admins: Add doctors and manage system.

## Troubleshooting

- If database issues, delete `hospital_queue.db` and restart.
- Ensure all dependencies are installed.