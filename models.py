from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    role = db.Column(db.String(50), nullable=False)  # 'patient', 'doctor', 'admin'
    name = db.Column(db.String(150), nullable=False)
    phone_number = db.Column(db.String(50), nullable=True)
    profile_picture = db.Column(db.String(250), nullable=True)
    date_of_birth = db.Column(db.Date, nullable=True)
    gender = db.Column(db.String(50), nullable=True)
    blood_group = db.Column(db.String(10), nullable=True)
    allergies = db.Column(db.Text, nullable=True)
    medical_conditions = db.Column(db.Text, nullable=True)
    emergency_contact = db.Column(db.String(150), nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Doctor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    specialty = db.Column(db.String(150), nullable=False)
    consultation_time = db.Column(db.Integer, default=15)  # minutes
    consultation_fee = db.Column(db.Float, default=500.0)
    photo_url = db.Column(db.String(250), nullable=True)
    bio = db.Column(db.Text, nullable=True)
    contributions = db.Column(db.Text, nullable=True)
    qualifications = db.Column(db.Text, nullable=True)  # e.g., MBBS, MD, etc.
    experience_years = db.Column(db.Integer, default=0)  # years of experience
    allows_telemedicine = db.Column(db.Boolean, default=False)  # can offer virtual consultations

    user = db.relationship('User', backref=db.backref('doctor', uselist=False))

class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctor.id'), nullable=False)
    family_member_id = db.Column(db.Integer, db.ForeignKey('family_member.id'), nullable=True)
    appointment_time = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(50), default='scheduled')  # 'scheduled', 'checked_in', 'completed', 'cancelled', 'missed'
    phone_number = db.Column(db.String(50), nullable=True)
    appointment_code = db.Column(db.String(20), nullable=False, default='')
    notes = db.Column(db.Text, nullable=True)
    diagnosis = db.Column(db.Text, nullable=True)
    prescription = db.Column(db.Text, nullable=True)
    follow_up_advice = db.Column(db.Text, nullable=True)
    is_emergency = db.Column(db.Boolean, default=False)  # emergency appointment
    consultation_type = db.Column(db.String(20), default='in-person')  # 'in-person' or 'virtual'
    is_follow_up = db.Column(db.Boolean, default=False)  # is this a follow-up appointment
    follow_up_of = db.Column(db.Integer, db.ForeignKey('appointment.id'), nullable=True)  # reference to original appointment
    amount_due = db.Column(db.Float, default=0.0)
    payment_status = db.Column(db.String(20), default='pending')  # 'pending', 'paid', 'failed'
    payment_method = db.Column(db.String(50), nullable=True)
    payment_reference = db.Column(db.String(100), nullable=True)
    paid_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    patient = db.relationship('User', foreign_keys=[patient_id])
    doctor_rel = db.relationship('Doctor', backref=db.backref('appointments', lazy=True))
    family_member = db.relationship('FamilyMember', foreign_keys=[family_member_id], backref=db.backref('appointments', lazy=True))
    original_appointment = db.relationship('Appointment', remote_side=[id], backref='follow_ups')

class AppointmentSlot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctor.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    is_available = db.Column(db.Boolean, default=True)

    doctor = db.relationship('Doctor', backref=db.backref('slots', lazy=True))

class QueueRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctor.id'), nullable=False)
    queue_number = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(50), default='waiting')  # 'waiting', 'in_consultation', 'completed', 'cancelled', 'expired'
    estimated_wait = db.Column(db.Integer, default=0)  # minutes
    priority = db.Column(db.String(20), default='normal')  # 'emergency', 'priority', 'normal'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship('User', foreign_keys=[patient_id])
    doctor_rel = db.relationship('Doctor', backref=db.backref('queue_records', lazy=True))

class FamilyMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    relationship = db.Column(db.String(100), nullable=False)
    phone_number = db.Column(db.String(50), nullable=True)
    date_of_birth = db.Column(db.Date, nullable=True)
    gender = db.Column(db.String(50), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship('User', backref=db.backref('family_members', lazy=True))

class HealthRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    shared_with_doctor_id = db.Column(db.Integer, db.ForeignKey('doctor.id'), nullable=True)
    title = db.Column(db.String(200), nullable=False)
    record_type = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    file_name = db.Column(db.String(250), nullable=False)
    original_filename = db.Column(db.String(250), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship('User', backref=db.backref('health_records', lazy=True))
    shared_with_doctor = db.relationship('Doctor', foreign_keys=[shared_with_doctor_id])

class LabTestBooking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    test_name = db.Column(db.String(200), nullable=False)
    preferred_date = db.Column(db.Date, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), default='requested')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship('User', backref=db.backref('lab_test_bookings', lazy=True))

class InsuranceProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, unique=True)
    provider_name = db.Column(db.String(200), nullable=False)
    policy_number = db.Column(db.String(120), nullable=False)
    member_id = db.Column(db.String(120), nullable=True)
    coverage_level = db.Column(db.String(100), nullable=True)
    coverage_notes = db.Column(db.Text, nullable=True)
    digital_card_number = db.Column(db.String(40), nullable=False)
    expiry_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    patient = db.relationship('User', backref=db.backref('insurance_profile', uselist=False))

class EmergencyAlert(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    location_text = db.Column(db.String(250), nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    contacted_number = db.Column(db.String(50), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship('User', backref=db.backref('emergency_alerts', lazy=True))

class WellnessMetric(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    weight_kg = db.Column(db.Float, nullable=True)
    height_cm = db.Column(db.Float, nullable=True)
    blood_pressure_systolic = db.Column(db.Integer, nullable=True)
    blood_pressure_diastolic = db.Column(db.Integer, nullable=True)
    blood_sugar_mg_dl = db.Column(db.Float, nullable=True)
    bmi = db.Column(db.Float, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    measured_at = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship('User', backref=db.backref('wellness_metrics', lazy=True))

class BloodDonor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    blood_group = db.Column(db.String(10), nullable=False)
    phone_number = db.Column(db.String(50), nullable=True)
    city = db.Column(db.String(120), nullable=True)
    available = db.Column(db.Boolean, default=True)
    last_donation_date = db.Column(db.Date, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class BloodDonationRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    blood_group = db.Column(db.String(10), nullable=False)
    hospital_name = db.Column(db.String(200), nullable=False)
    urgency_level = db.Column(db.String(50), default='normal')
    message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship('User', backref=db.backref('blood_donation_requests', lazy=True))

class VaccinationRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    vaccine_name = db.Column(db.String(200), nullable=False)
    dose_number = db.Column(db.String(50), nullable=True)
    vaccination_date = db.Column(db.Date, nullable=True)
    next_due_date = db.Column(db.Date, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship('User', backref=db.backref('vaccination_records', lazy=True))

class ForumPost(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship('User', backref=db.backref('forum_posts', lazy=True))

class ForumReply(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('forum_post.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    post = db.relationship('ForumPost', backref=db.backref('replies', lazy=True, cascade='all, delete-orphan'))
    user = db.relationship('User', backref=db.backref('forum_replies', lazy=True))

class WearableSnapshot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    device_name = db.Column(db.String(150), nullable=False)
    heart_rate = db.Column(db.Integer, nullable=True)
    steps = db.Column(db.Integer, nullable=True)
    sleep_hours = db.Column(db.Float, nullable=True)
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship('User', backref=db.backref('wearable_snapshots', lazy=True))

class LanguagePreference(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, unique=True)
    preferred_language = db.Column(db.String(50), nullable=False, default='English')
    voice_assistance_enabled = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    patient = db.relationship('User', backref=db.backref('language_preference', uselist=False))

class TelemedicineSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointment.id'), nullable=False, unique=True)
    meeting_code = db.Column(db.String(30), nullable=False)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text, nullable=True)

    appointment = db.relationship('Appointment', backref=db.backref('telemedicine_session', uselist=False))

class PharmacyMedicine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    price = db.Column(db.Float, nullable=False)
    dosage = db.Column(db.String(100), nullable=True)
    requires_prescription = db.Column(db.Boolean, default=False)
    in_stock = db.Column(db.Boolean, default=True)
    stock_quantity = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class PharmacyOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    medicine_id = db.Column(db.Integer, db.ForeignKey('pharmacy_medicine.id'), nullable=False)
    quantity = db.Column(db.Integer, default=1)
    prescription_file = db.Column(db.String(250), nullable=True)
    delivery_address = db.Column(db.String(300), nullable=True)
    status = db.Column(db.String(50), default='pending')  # pending, approved, shipped, delivered, cancelled
    total_price = db.Column(db.Float, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    patient = db.relationship('User', backref=db.backref('pharmacy_orders', lazy=True))
    medicine = db.relationship('PharmacyMedicine', backref=db.backref('orders', lazy=True))

class ChatConsultation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctor.id'), nullable=True)
    topic = db.Column(db.String(200), nullable=False)
    status = db.Column(db.String(50), default='active')  # active, closed, waiting_response
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    patient = db.relationship('User', backref=db.backref('chat_consultations', lazy=True))
    doctor_rel = db.relationship('Doctor', backref=db.backref('chat_consultations', lazy=True))

class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    consultation_id = db.Column(db.Integer, db.ForeignKey('chat_consultation.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message = db.Column(db.Text, nullable=False)
    attachment_filename = db.Column(db.String(250), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    consultation = db.relationship('ChatConsultation', backref=db.backref('messages', lazy=True, cascade='all, delete-orphan'))
    sender = db.relationship('User', backref=db.backref('chat_messages', lazy=True))
