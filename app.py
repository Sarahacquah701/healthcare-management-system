from flask import Flask, render_template, request, redirect, url_for, flash as flask_flash, session, jsonify, send_from_directory
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from functools import wraps
from email.message import EmailMessage
from models import db, User, Doctor, Appointment, QueueRecord, AppointmentSlot, FamilyMember, HealthRecord, LabTestBooking, InsuranceProfile, EmergencyAlert, WellnessMetric, BloodDonor, BloodDonationRequest, VaccinationRecord, ForumPost, ForumReply, WearableSnapshot, LanguagePreference, TelemedicineSession, PharmacyMedicine, PharmacyOrder, ChatConsultation, ChatMessage, DoctorReview, NewsletterSubscriber, Notification, MedicationReminder, Announcement
import re
import json
from sqlalchemy import text
from uuid import uuid4
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
import os
import smtplib
from pathlib import Path

try:
    import jwt
    jwt_available = True
except Exception:
    jwt = None
    jwt_available = False

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

try:
    from flask_socketio import SocketIO, join_room, leave_room, emit
    socketio_available = True
except Exception:
    socketio_available = False

app = Flask(__name__)

UPLOAD_BASE = "/tmp/uploads"


def is_serverless_environment():
    return bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME") or os.environ.get("AWS_EXECUTION_ENV"))

os.makedirs(UPLOAD_BASE, exist_ok=True)
app.config['SECRET_KEY'] = 'your_secret_key_here'
app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY', app.config['SECRET_KEY'])
app.config['JWT_ACCESS_TOKEN_EXPIRES_MINUTES'] = int(os.environ.get('JWT_ACCESS_TOKEN_EXPIRES_MINUTES', '60'))
app.config['SMTP_HOST'] = os.environ.get('SMTP_HOST', '')
app.config['SMTP_PORT'] = int(os.environ.get('SMTP_PORT', '587'))
app.config['SMTP_USERNAME'] = os.environ.get('SMTP_USERNAME', '')
app.config['SMTP_PASSWORD'] = os.environ.get('SMTP_PASSWORD', '')
app.config['SMTP_USE_TLS'] = os.environ.get('SMTP_USE_TLS', '1') == '1'
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER', 'no-reply@healthflow.local')
# Supported locales for the app and JSON-backed translations.
app.config['BABEL_DEFAULT_LOCALE'] = 'en'
app.config['BABEL_SUPPORTED_LOCALES'] = ['en', 'fr', 'es', 'hi', 'zh', 'ko', 'tw', 'ha']
app.config['BABEL_TRANSLATION_DIRECTORIES'] = 'translations'
app.config['TRANSLATION_FILES_DIR'] = os.path.join(app.root_path, 'static', 'i18n')

def get_database_uri():
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return database_url.replace("postgres://", "postgresql://", 1)

    is_serverless = bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME") or os.environ.get("AWS_EXECUTION_ENV"))
    if is_serverless:
        db_path = "/tmp/hospital_queue.db"
    else:
        project_root = Path(__file__).resolve().parent
        db_path = str(project_root / "hospital_queue.db")
    return "sqlite:///" + db_path


app.config["SQLALCHEMY_DATABASE_URI"] = get_database_uri()

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['APP_INITIALIZED'] = False
app.config['HEALTH_RECORD_UPLOAD_FOLDER'] = os.path.join(UPLOAD_BASE, 'uploads', 'health_records')
os.makedirs(app.config['HEALTH_RECORD_UPLOAD_FOLDER'], exist_ok=True)
app.config['CHAT_UPLOAD_FOLDER'] = os.path.join(UPLOAD_BASE, 'uploads', 'chat_attachments')
os.makedirs(app.config['CHAT_UPLOAD_FOLDER'], exist_ok=True)

_translation_cache = {}

LANGUAGE_OPTIONS = [
    {'code': 'en', 'name': 'English'},
    {'code': 'fr', 'name': 'Francais'},
    {'code': 'es', 'name': 'Espanol'},
    {'code': 'hi', 'name': 'Hindi'},
    {'code': 'zh', 'name': 'Chinese'},
    {'code': 'ko', 'name': 'Korean'},
    {'code': 'tw', 'name': 'Twi'},
    {'code': 'ha', 'name': 'Hausa'},
]

LANGUAGE_NAME_TO_CODE = {
    'english': 'en',
    'francais': 'fr',
    'français': 'fr',
    'french': 'fr',
    'espanol': 'es',
    'español': 'es',
    'spanish': 'es',
    'hindi': 'hi',
    'chinese': 'zh',
    'korean': 'ko',
    'twi': 'tw',
    'hausa': 'ha',
}


def normalize_locale(lang):
    supported = app.config.get('BABEL_SUPPORTED_LOCALES', ['en'])
    if not lang:
        return app.config.get('BABEL_DEFAULT_LOCALE', 'en')
    cleaned = str(lang).strip()
    lowered = cleaned.lower()
    code = LANGUAGE_NAME_TO_CODE.get(lowered, lowered.split('-')[0].split('_')[0])
    return code if code in supported else app.config.get('BABEL_DEFAULT_LOCALE', 'en')


def load_translation_catalog(lang):
    lang = normalize_locale(lang)
    if lang in _translation_cache:
        return _translation_cache[lang]
    path = os.path.join(app.config['TRANSLATION_FILES_DIR'], f'{lang}.json')
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            catalog = json.load(handle)
    except Exception:
        catalog = {}
    _translation_cache[lang] = catalog
    return catalog


def get_locale():
    try:
        if current_user.is_authenticated:
            pref = get_language_preference(current_user.id)
            if pref and pref.preferred_language:
                return normalize_locale(pref.preferred_language)
    except Exception:
        pass
    return normalize_locale(session.get('lang') or request.cookies.get('lang') or app.config.get('BABEL_DEFAULT_LOCALE', 'en'))


def _(message, **variables):
    if message is None:
        return ''
    source_text = str(message)
    lang = get_locale()
    catalog = load_translation_catalog(lang)
    translated = catalog.get(source_text) or catalog.get(source_text.strip()) or source_text
    if variables:
        try:
            translated = translated % variables
        except Exception:
            try:
                translated = translated.format(**variables)
            except Exception:
                pass
    return translated


def flash(message, category='message'):
    flask_flash(_(message), category)

# Ensure the gettext function is available inside Jinja templates as a global
app.jinja_env.globals.update(_=_)


def get_token_serializer(salt):
    return URLSafeTimedSerializer(app.config['SECRET_KEY'], salt=salt)


def create_email_verification_token(user):
    token = get_token_serializer('email-verification').dumps({'user_id': user.id, 'email': user.email})
    user.email_verification_token = token
    return token


def create_password_reset_token(user):
    return get_token_serializer('password-reset').dumps({'user_id': user.id, 'email': user.email})


def verify_timed_token(token, salt, max_age=24 * 60 * 60):
    serializer = get_token_serializer(salt)
    try:
        return serializer.loads(token, max_age=max_age)
    except SignatureExpired:
        return None
    except BadSignature:
        return None


def send_security_email(recipient_email, subject, body):
    host = app.config.get('SMTP_HOST')
    username = app.config.get('SMTP_USERNAME')
    password = app.config.get('SMTP_PASSWORD')
    if not host or not username or not password:
        app.logger.info('%s to %s: %s', subject, recipient_email, body)
        return False

    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = app.config.get('MAIL_DEFAULT_SENDER')
    message['To'] = recipient_email
    message.set_content(body)

    with smtplib.SMTP(host, app.config.get('SMTP_PORT', 587)) as client:
        if app.config.get('SMTP_USE_TLS', True):
            client.starttls()
        client.login(username, password)
        client.send_message(message)
    return True


def issue_jwt_token(user, minutes=None):
    expires_minutes = minutes or app.config.get('JWT_ACCESS_TOKEN_EXPIRES_MINUTES', 60)
    payload = {
        'sub': str(user.id),
        'email': user.email,
        'username': user.username,
        'role': user.role,
        'name': user.name,
        'iat': datetime.utcnow(),
        'exp': datetime.utcnow() + timedelta(minutes=expires_minutes),
    }
    if jwt_available:
        token = jwt.encode(payload, app.config['JWT_SECRET_KEY'], algorithm='HS256')
        return token.decode('utf-8') if isinstance(token, bytes) else token

    serializer = get_token_serializer('jwt-fallback')
    return serializer.dumps(payload)


def decode_jwt_token(token):
    if jwt_available:
        return jwt.decode(token, app.config['JWT_SECRET_KEY'], algorithms=['HS256'])
    serializer = get_token_serializer('jwt-fallback')
    return serializer.loads(token, max_age=app.config.get('JWT_ACCESS_TOKEN_EXPIRES_MINUTES', 60) * 60)


def jwt_required_api(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        token = None
        if auth_header.startswith('Bearer '):
            token = auth_header.split(' ', 1)[1].strip()
        if not token:
            return jsonify({'error': _('Missing access token.')}), 401
        try:
            payload = decode_jwt_token(token)
        except Exception:
            return jsonify({'error': _('Invalid or expired access token.')}), 401
        user = User.query.get(int(payload.get('sub')))
        if not user:
            return jsonify({'error': _('User not found.')}), 404
        request.jwt_user = user
        request.jwt_payload = payload
        return view_func(*args, **kwargs)

    return wrapper


def send_verification_email(user):
    token = create_email_verification_token(user)
    db.session.add(user)
    db.session.commit()
    link = url_for('verify_email', token=token, _external=True)
    send_security_email(
        user.email,
        _('Verify your HealthFlow account'),
        f'Hello {user.name},\n\nPlease verify your email address by opening this link:\n{link}\n\nIf you did not create this account, you can ignore this message.'
    )
    return link


def send_password_reset_email(user):
    token = create_password_reset_token(user)
    link = url_for('reset_password_token', token=token, _external=True)
    send_security_email(
        user.email,
        _('Reset your HealthFlow password'),
        f'Hello {user.name},\n\nReset your password by opening this link:\n{link}\n\nThis link expires in 24 hours.'
    )
    return link


def create_notification(recipient_user_id, title, message, category='general', is_read=False):
    notification = Notification(
        recipient_user_id=recipient_user_id,
        title=title,
        message=message,
        category=category,
        is_read=is_read,
    )
    db.session.add(notification)
    db.session.commit()
    return notification


def get_user_notifications(user_id, limit=10):
    return Notification.query.filter_by(recipient_user_id=user_id).order_by(Notification.created_at.desc()).limit(limit).all()


def get_latest_doctor_review_summary(doctor_id):
    reviews = DoctorReview.query.filter_by(doctor_id=doctor_id).order_by(DoctorReview.created_at.desc()).all()
    if not reviews:
        return {'rating': None, 'count': 0, 'reviews': []}
    average = sum(review.rating for review in reviews) / len(reviews)
    return {'rating': round(average, 1), 'count': len(reviews), 'reviews': reviews[:5]}


def get_or_create_doctor_profile(user=None):
    if not user:
        user = current_user
    if not user or user.role != 'doctor':
        return None

    doctor = Doctor.query.filter_by(user_id=user.id).first()
    if doctor is None:
        doctor = Doctor(user_id=user.id, specialty='General', consultation_time=15, consultation_fee=500.0)
        db.session.add(doctor)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            doctor = Doctor.query.filter_by(user_id=user.id).first()
    return doctor


@app.context_processor
def inject_supported_locales():
    try:
        locales = app.config.get('BABEL_SUPPORTED_LOCALES', ['en'])
    except Exception:
        locales = ['en']
    current_lang = get_locale()
    return dict(supported_locales=locales, current_lang=current_lang, language_options=LANGUAGE_OPTIONS)

# Initialize SocketIO if available
if socketio_available:
    socketio = SocketIO(app, cors_allowed_origins='*')



@app.route('/set_language/<lang>')
def set_language(lang):
    lang = normalize_locale(lang)
    session['lang'] = lang
    try:
        if current_user.is_authenticated:
            pref = get_language_preference(current_user.id)
            if pref:
                pref.preferred_language = lang
            else:
                lp = LanguagePreference(patient_id=current_user.id, preferred_language=lang)
                db.session.add(lp)
            db.session.commit()
    except Exception:
        pass
    wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', '')
    response = jsonify({'success': True, 'lang': lang}) if wants_json else redirect(request.referrer or url_for('home'))
    response.set_cookie('lang', lang, max_age=90*24*60*60)
    return response

db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def generate_appointment_code():
    code = uuid4().hex[:8].upper()
    while Appointment.query.filter_by(appointment_code=code).first():
        code = uuid4().hex[:8].upper()
    return code

def parse_form_time(time_str):
    for time_format in ('%H:%M', '%H:%M:%S'):
        try:
            return datetime.strptime(time_str, time_format).time()
        except ValueError:
            pass
    raise ValueError(f"Invalid time format: {time_str}")

def build_default_time_slots():
    slots = []
    for hour in (9, 10, 11, 14, 15, 16):
        for minute in (0, 30):
            slots.append(datetime.strptime(f'{hour:02d}:{minute:02d}', '%H:%M').time())
    return slots

def get_available_times(doctor_id, appointment_date, exclude_appointment_id=None):
    slots = AppointmentSlot.query.filter_by(
        doctor_id=doctor_id,
        date=appointment_date,
        is_available=True
    ).order_by(AppointmentSlot.start_time).all()

    times = []
    if slots:
        doctor = Doctor.query.get(doctor_id)
        step = timedelta(minutes=doctor.consultation_time if doctor else 30)
        for slot in slots:
            current = datetime.combine(appointment_date, slot.start_time)
            end = datetime.combine(appointment_date, slot.end_time)
            while current + step <= end:
                times.append(current.time())
                current += step
    else:
        times = build_default_time_slots()

    booked_query = Appointment.query.filter(
        Appointment.doctor_id == doctor_id,
        Appointment.status.in_(['scheduled', 'checked_in']),
        Appointment.appointment_time >= datetime.combine(appointment_date, datetime.min.time()),
        Appointment.appointment_time <= datetime.combine(appointment_date, datetime.max.time())
    )
    if exclude_appointment_id:
        booked_query = booked_query.filter(Appointment.id != exclude_appointment_id)
    booked_times = {appointment.appointment_time.time().replace(second=0, microsecond=0) for appointment in booked_query.all()}

    return [time for time in times if time.replace(second=0, microsecond=0) not in booked_times]

def validate_appointment_time(doctor_id, appointment_time, exclude_appointment_id=None):
    if appointment_time < datetime.utcnow():
        return False, 'Please choose a future appointment time.'
    doctor = Doctor.query.get(doctor_id)
    if not doctor:
        return False, 'Selected doctor was not found.'
    available_times = get_available_times(doctor_id, appointment_time.date(), exclude_appointment_id)
    selected_time = appointment_time.time().replace(second=0, microsecond=0)
    if selected_time not in [time.replace(second=0, microsecond=0) for time in available_times]:
        return False, 'That time is no longer available. Please choose another slot.'
    return True, ''

def get_doctor_availability_calendar(doctor_id, days_ahead=7):
    calendar = []
    today = datetime.utcnow().date()
    for offset in range(days_ahead):
        current_date = today + timedelta(days=offset)
        slots = [time.strftime('%I:%M %p') for time in get_available_times(doctor_id, current_date)[:4]]
        calendar.append({
            'date': current_date,
            'display': 'Today' if offset == 0 else current_date.strftime('%a, %d %b'),
            'slots': slots
        })
    return calendar

def get_next_available_slot_label(availability_calendar):
    for day in availability_calendar:
        if day['slots']:
            return f"{day['display']} at {day['slots'][0]}"
    return 'No open slots in the next 7 days'

def get_patient_family_members(patient_id):
    return FamilyMember.query.filter_by(patient_id=patient_id).order_by(FamilyMember.created_at.desc()).all()

def get_patient_insurance_profile(patient_id):
    return InsuranceProfile.query.filter_by(patient_id=patient_id).first()

def get_language_preference(patient_id):
    return LanguagePreference.query.filter_by(patient_id=patient_id).first()

def get_latest_wellness_metric(patient_id):
    return WellnessMetric.query.filter_by(patient_id=patient_id).order_by(WellnessMetric.measured_at.desc()).first()

def calculate_bmi(weight_kg, height_cm):
    if not weight_kg or not height_cm:
        return None
    height_m = height_cm / 100.0
    if height_m <= 0:
        return None
    return round(weight_kg / (height_m * height_m), 1)

def get_wellness_recommendations(metric):
    tips = []
    if not metric:
        return [
            'Start tracking your weight, blood pressure, blood sugar, and BMI to see health trends over time.',
            'Add a few measurements first so the dashboard can suggest more personalized guidance.'
        ]
    if metric.bmi is not None:
        if metric.bmi >= 30:
            tips.append('Focus on balanced meals and gradual activity goals. A clinician can help you set a safe plan.')
        elif metric.bmi >= 25:
            tips.append('Add regular walking and reduce sugary drinks to support healthier weight trends.')
        else:
            tips.append('Maintain your current activity routine and keep logging metrics consistently.')
    if metric.blood_pressure_systolic and metric.blood_pressure_systolic >= 140:
        tips.append('Your blood pressure reading is elevated. Reduce sodium, hydrate well, and follow up with a clinician.')
    if metric.blood_sugar_mg_dl and metric.blood_sugar_mg_dl >= 140:
        tips.append('Your blood sugar reading is above the usual range. Consider a doctor review and monitor meals closely.')
    if not tips:
        tips.append('Keep tracking weekly so the app can highlight longer-term health trends.')
    return tips[:4]

def get_blood_donor_matches(blood_group, city=None):
    query = BloodDonor.query.filter(BloodDonor.available.is_(True), BloodDonor.blood_group == blood_group)
    if city:
        query = query.filter(BloodDonor.city.ilike(f'%{city}%'))
    return query.order_by(BloodDonor.created_at.desc()).all()

def allowed_health_record_file(filename):
    allowed_extensions = {'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions

def allowed_profile_image(filename):
    allowed_extensions = {'png', 'jpg', 'jpeg', 'gif'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions

def appointment_checkin_message(appointment):
    now = datetime.utcnow()
    starts_at = appointment.appointment_time - timedelta(minutes=30)
    ends_at = appointment.appointment_time + timedelta(minutes=30)
    if now < starts_at:
        return f'Check-in opens at {starts_at.strftime("%I:%M %p")}.'
    if now > ends_at:
        return 'The check-in window has closed. Please contact reception.'
    return ''

def build_patient_notifications(appointments, patient_queue):
    notifications = []
    now = datetime.utcnow()
    tomorrow = (now + timedelta(days=1)).date()

    for appointment in appointments:
        if appointment.status != 'scheduled':
            continue
        if appointment.appointment_time.date() == tomorrow:
            notifications.append(f'Reminder: appointment with Dr. {appointment.doctor_rel.user.name} is tomorrow at {appointment.appointment_time.strftime("%I:%M %p")}.')
        elif appointment.appointment_time.date() == now.date():
            checkin_message = appointment_checkin_message(appointment)
            if checkin_message:
                notifications.append(f'Today: appointment with Dr. {appointment.doctor_rel.user.name} at {appointment.appointment_time.strftime("%I:%M %p")}. {checkin_message}')
            else:
                notifications.append(f'Check-in is open for Dr. {appointment.doctor_rel.user.name}.')

    for item in patient_queue:
        if item['record'].status == 'waiting':
            notifications.append(f'Queue update: you are #{item["position"]} for Dr. {item["record"].doctor_rel.user.name}.')
        elif item['record'].status == 'in_consultation':
            notifications.append(f'You are currently in consultation with Dr. {item["record"].doctor_rel.user.name}.')

    return notifications[:5]

def build_patient_reminders(appointments, patient_queue):
    reminders = []
    now = datetime.utcnow()
    today = now.date()
    upcoming_window = today + timedelta(days=7)

    for appointment in appointments:
        if appointment.status != 'scheduled':
            continue

        appointment_date = appointment.appointment_time.date()
        if appointment_date == today:
            checkin_message = appointment_checkin_message(appointment)
            reminders.append({
                'title': 'Today',
                'message': f'Dr. {appointment.doctor_rel.user.name} at {appointment.appointment_time.strftime("%I:%M %p")}. {checkin_message or "Check-in is open now."}',
                'variant': 'danger' if appointment.is_emergency else 'info',
                'action_label': 'View Appointment',
                'action_url': url_for('appointment_details', appointment_id=appointment.id)
            })
        elif appointment_date == today + timedelta(days=1):
            reminders.append({
                'title': 'Tomorrow',
                'message': f'Dr. {appointment.doctor_rel.user.name} at {appointment.appointment_time.strftime("%I:%M %p")}. Please keep your phone available.',
                'variant': 'warning',
                'action_label': 'Reschedule',
                'action_url': url_for('reschedule_appointment', appointment_id=appointment.id)
            })
        elif today < appointment_date <= upcoming_window:
            reminders.append({
                'title': 'Upcoming Visit',
                'message': f'Dr. {appointment.doctor_rel.user.name} on {appointment.appointment_time.strftime("%b %d at %I:%M %p")}.',
                'variant': 'secondary',
                'action_label': 'View Details',
                'action_url': url_for('appointment_details', appointment_id=appointment.id)
            })

        if appointment.payment_status != 'paid' and appointment.amount_due > 0:
            reminders.append({
                'title': 'Payment Pending',
                'message': f'₹{appointment.amount_due:.2f} is due for Dr. {appointment.doctor_rel.user.name}.',
                'variant': 'warning',
                'action_label': 'Pay Now',
                'action_url': url_for('appointment_details', appointment_id=appointment.id)
            })

    for item in patient_queue:
        if item['record'].status == 'waiting':
            reminders.append({
                'title': 'Queue Update',
                'message': f'You are #{item["position"]} for Dr. {item["record"].doctor_rel.user.name} with an estimated wait of {item["record"].estimated_wait} minutes.',
                'variant': 'info',
                'action_label': 'Open Dashboard',
                'action_url': url_for('dashboard')
            })
        elif item['record'].status == 'in_consultation':
            reminders.append({
                'title': 'In Consultation',
                'message': f'You are currently in consultation with Dr. {item["record"].doctor_rel.user.name}.',
                'variant': 'success',
                'action_label': 'View Dashboard',
                'action_url': url_for('dashboard')
            })

    if not reminders:
        reminders.append({
            'title': 'No Active Reminders',
            'message': 'You do not have any active appointment or queue reminders right now.',
            'variant': 'success',
            'action_label': 'Book Appointment',
            'action_url': url_for('book_appointment')
        })

    return reminders[:8]


def generate_ai_response(message):
    normalized = message.strip().lower()
    if not normalized:
        return _('I am here to help. Please type your question or tell me how I can assist you.')

    if 'appointment' in normalized or 'book' in normalized:
        return _('You can book an appointment from your dashboard or the Book Appointment page. Let me know if you want me to help you find a doctor or available slots.')
    if 'doctor' in normalized or 'specialist' in normalized:
        return _('Ask me about doctor availability, specialties, telemedicine options, or emergency consultation support.')
    if 'payment' in normalized or 'bill' in normalized or 'invoice' in normalized:
        return _('Payments are handled through the booking flow. After scheduling, you can complete payment for your appointment from the appointment details page.')
    if 'emergency' in normalized or 'urgent' in normalized:
        return _('If this is an urgent medical issue, please contact emergency services immediately. For emergency bookings, choose the emergency option when you book your appointment.')
    if 'telemedicine' in normalized or 'virtual' in normalized or 'online' in normalized:
        return _('Some doctors offer telemedicine. If your selected doctor supports it, you will see the virtual consultation option on the booking page.')
    if 'follow-up' in normalized or 'follow up' in normalized:
        return _('You can schedule a follow-up appointment after your consultation from the appointment details page or through the follow-up scheduler.')
    if 'hello' in normalized or 'hi' in normalized or 'hey' in normalized:
        return _('Hello! I am your HealthFlow assistant. How can I help you today?')

    return _('I am still learning. Please ask me about appointments, doctors, telemedicine, payment, or emergency booking.')

def generate_support_response(message):
    normalized = message.strip().lower()
    if not normalized:
        return _('Hello! I can help with appointments, billing, doctor search, reminders, and general hospital services.')

    if any(term in normalized for term in ('emergency', 'urgent', 'chest pain', 'breathing', 'stroke', 'unresponsive', 'severe bleeding')):
        return _('If this is a medical emergency, call local emergency services immediately or go to the nearest emergency department. This chat cannot replace urgent care.')
    if 'billing' in normalized or 'payment' in normalized or 'receipt' in normalized or 'invoice' in normalized:
        return _('You can review payments and receipts from the Billing page. If a receipt is missing, open the related appointment and verify the payment status.')
    if 'doctor' in normalized or 'specialist' in normalized or 'cardiology' in normalized or 'dermatology' in normalized or 'pediatrics' in normalized:
        return _('Use the Doctors directory to search by specialty, fees, and telemedicine availability. You can also open a doctor profile to review availability.')
    if 'appointment' in normalized or 'book' in normalized or 'reschedule' in normalized or 'cancel' in normalized:
        return _('Appointments can be booked, rescheduled, or cancelled from your dashboard and the booking pages. Emergency booking is available for urgent requests.')
    if 'reminder' in normalized or 'notification' in normalized:
        return _('Open the Reminders page to see appointment alerts, queue updates, and payment reminders in one place.')
    if 'history' in normalized or 'record' in normalized or 'medical' in normalized:
        return _('Open Medical History to review your appointments, queue visits, and profile details.')

    return _('I can help with bookings, doctors, reminders, billing, and support requests. If your concern is medical, please use the symptom checker or contact a clinician.')

def analyze_symptoms(symptoms, duration='', age_group='', fever=False, breathing=False, chest_pain=False, patient_allergies='', patient_age=None):
    normalized = re.sub(r'[^a-z0-9 ]+', ' ', (symptoms or '').strip().lower())

    def contains_any(terms):
        return any(term in normalized for term in terms)

    # Basic emergency checks
    if chest_pain or breathing or contains_any(('stroke', 'seizure', 'fainting', 'unresponsive', 'severe bleeding', 'anaphylaxis', 'sudden weakness', 'sudden numbness')):
        return {
            'risk_level': 'Emergency',
            'summary': 'These symptoms suggest a serious condition that may require immediate medical attention.',
            'specialty': 'Emergency Care',
            'advice': 'Go to the nearest emergency department or call emergency services now.',
            'action_label': 'Emergency Booking',
            'action_url': url_for('book_appointment', emergency='1'),
            'extra': 'This tool is not a replacement for emergency care. If in doubt, seek help immediately.',
            'suggested_meds': []
        }

    # Match specialty
    matched_specialty = 'General Medicine'
    match_priority = [
        ('Cardiology', ('heart', 'palpitations', 'blood pressure', 'bp', 'chest discomfort', 'chest tightness')),
        ('Dermatology', ('skin', 'rash', 'itch', 'acne', 'eczema', 'hives', 'blister')),
        ('Pediatrics', ('child', 'baby', 'infant', 'kid', 'pediatric', 'toddler')),
        ('Neurology', ('headache', 'migraine', 'dizziness', 'numbness', 'seizure')),
        ('Gastroenterology', ('stomach', 'nausea', 'vomiting', 'diarrhea', 'acid reflux', 'abdominal', 'bloating')),
        ('Orthopedics', ('bone', 'joint', 'sprain', 'fracture', 'back', 'neck', 'knee', 'arthritis', 'shoulder')),
        ('Ophthalmology', ('eye', 'vision', 'blurry', 'red eye', 'eye pain')),
        ('ENT', ('ear', 'nose', 'throat', 'sinus', 'tonsil', 'hoarseness')),
        ('Urology', ('urinary', 'urine', 'burning', 'blood in urine', 'kidney'))
    ]
    for specialty, terms in match_priority:
        if contains_any(terms):
            matched_specialty = specialty
            break

    # Medication suggestion rules (informational only)
    med_rules = [
        ({'keywords': ('fever', 'body aches', 'headache')}, {'name': 'Paracetamol', 'dose': '500 mg every 4-6 hours as needed (max 4 g/day)', 'otc': True, 'caution': 'Avoid if allergic to paracetamol; consult for children/pregnancy.'}),
        ({'keywords': ('pain', 'ache', 'sprain', 'back', 'joint')}, {'name': 'Ibuprofen', 'dose': '200-400 mg every 6-8 hours as needed', 'otc': True, 'caution': 'Avoid if history of peptic ulcer, kidney disease, or NSAID allergy. Not for late pregnancy.'}),
        ({'keywords': ('cough', 'cold', 'sore throat')}, {'name': 'Lozenges / throat analgesic', 'dose': 'Use as directed on pack', 'otc': True, 'caution': 'Check ingredients for known allergies.'}),
        ({'keywords': ('runny nose', 'congestion', 'sinus')}, {'name': 'Oral decongestant (pseudoephedrine)', 'dose': 'Follow label', 'otc': True, 'caution': 'Not for uncontrolled hypertension or certain heart conditions.'}),
        ({'keywords': ('diarrhea', 'loose stool')}, {'name': 'Oral rehydration solution (ORS)', 'dose': 'Rehydrate with fluids as directed', 'otc': True, 'caution': 'Seek medical care for children, high fevers, or bloody stools.'}),
        ({'keywords': ('heartburn', 'acid reflux', 'indigestion')}, {'name': 'Antacid', 'dose': 'Use as directed', 'otc': True, 'caution': 'Persistent symptoms need clinician review.'}),
        ({'keywords': ('rash', 'itch', 'eczema', 'hives')}, {'name': 'Topical hydrocortisone 1%', 'dose': 'Apply thinly to affected area 1-2 times daily', 'otc': True, 'caution': 'Avoid on infected skin; not for severe allergic reactions.'}),
        ({'keywords': ('allergy', 'sneezing', 'itchy eyes')}, {'name': 'Loratadine (antihistamine)', 'dose': '10 mg once daily', 'otc': True, 'caution': 'May cause drowsiness in some people.'})
    ]

    suggested = []
    allergies_lower = (patient_allergies or '').lower()
    for rule in med_rules:
        kws = rule[0]['keywords']
        if contains_any(kws):
            med = rule[1].copy()
            # Add a simple allergy check
            if any(allergy in allergies_lower for allergy in ('paracetamol', 'acetaminophen')) and med['name'].lower().startswith('paracetamol'):
                med['caution'] = med['caution'] + ' -- Patient-reported allergy to paracetamol.'
            suggested.append(med)

    # Build risk-based response
    if fever or contains_any(('fever', 'cough', 'cold', 'flu', 'sore throat', 'chills')):
        risk_level = 'Urgent' if fever and contains_any(('shortness of breath', 'wheezing', 'persistent cough', 'high fever')) else 'Soon'
        return {
            'risk_level': risk_level,
            'summary': 'Your symptoms may need a clinician review soon.' if risk_level == 'Soon' else 'Your symptoms may need prompt review by a clinician.',
            'specialty': matched_specialty,
            'advice': 'If symptoms persist, worsen, or include chest pain, breathing difficulty, confusion, or severe weakness, book care immediately.',
            'action_label': 'Find a Doctor',
            'action_url': url_for('doctors_directory', specialty=matched_specialty),
            'extra': 'Monitor your symptoms closely, stay hydrated, and rest. Seek emergency care if the condition worsens.',
            'suggested_meds': suggested
        }

    if contains_any(('shortness of breath', 'wheezing', 'stridor', 'chest tightness')):
        return {
            'risk_level': 'Emergency',
            'summary': 'Breathing-related symptoms need urgent medical attention.',
            'specialty': 'Emergency Care',
            'advice': 'Seek immediate care or call emergency services.',
            'action_label': 'Emergency Booking',
            'action_url': url_for('book_appointment', emergency='1'),
            'extra': 'Respiratory symptoms can deteriorate quickly. Do not delay.',
            'suggested_meds': []
        }

    if contains_any(('bleeding', 'blood', 'black stool', 'bloody stool', 'blood in vomit', 'pale', 'confusion')):
        return {
            'risk_level': 'Emergency',
            'summary': 'These symptoms may indicate a serious condition requiring urgent evaluation.',
            'specialty': 'Emergency Care',
            'advice': 'Go to the nearest emergency department or call emergency services.',
            'action_label': 'Emergency Booking',
            'action_url': url_for('book_appointment', emergency='1'),
            'extra': 'Do not wait if you are experiencing heavy bleeding, black stool, or fainting.',
            'suggested_meds': []
        }

    nonurgent_terms = ('mild', 'slight', 'low-grade', 'tolerable', 'occasional')
    if contains_any(nonurgent_terms) and not fever and not breathing and not chest_pain:
        return {
            'risk_level': 'General',
            'summary': 'Your symptoms seem mild and may be suited to routine care.',
            'specialty': matched_specialty,
            'advice': 'Schedule a routine appointment and follow up if symptoms worsen or do not improve.',
            'action_label': 'Book Appointment',
            'action_url': url_for('book_appointment'),
            'extra': 'This is a triage guide, not a medical diagnosis. If you are concerned, see a clinician.',
            'suggested_meds': suggested
        }

    if contains_any(('pain', 'ache', 'discomfort', 'swelling', 'redness', 'lump', 'burning')):
        return {
            'risk_level': 'Urgent',
            'summary': 'Your symptoms may need prompt medical review.',
            'specialty': matched_specialty,
            'advice': 'Book an appointment within 24-48 hours, especially if symptoms persist or worsen.',
            'action_label': 'Book Appointment',
            'action_url': url_for('book_appointment'),
            'extra': 'If pain becomes severe or new high-risk symptoms appear, seek urgent care.',
            'suggested_meds': suggested
        }

    return {
        'risk_level': 'General',
        'summary': 'Your symptoms do not appear to be an immediate emergency.',
        'specialty': matched_specialty,
        'advice': 'A routine appointment is usually appropriate. Follow up if symptoms persist or worsen.',
        'action_label': 'Book Appointment',
        'action_url': url_for('book_appointment'),
        'extra': 'This is not a diagnosis. Use it as a first-step guide for next actions.',
        'suggested_meds': suggested
    }


def mark_missed_appointments():
    now = datetime.utcnow()
    missed_appointments = Appointment.query.filter(
        Appointment.status == 'scheduled',
        Appointment.appointment_time < now - timedelta(minutes=30)
    ).all()
    if not missed_appointments:
        return
    for appointment in missed_appointments:
        appointment.status = 'missed'
        appointment.updated_at = now
    db.session.commit()

def expire_stale_waiting_patients():
    today_start = datetime.combine(datetime.utcnow().date(), datetime.min.time())
    stale_records = QueueRecord.query.filter(
        QueueRecord.status == 'waiting',
        QueueRecord.created_at < today_start
    ).all()
    if not stale_records:
        return

    doctor_ids = {record.doctor_id for record in stale_records}
    for record in stale_records:
        record.status = 'expired'
    db.session.commit()
    for doctor_id in doctor_ids:
        recalculate_wait_times(doctor_id)

def recalculate_wait_times(doctor_id):
    doctor = Doctor.query.get(doctor_id)
    if not doctor:
        return
    # Order by priority first (emergency > priority > normal), then by queue number
    priority_order = db.case(
        (QueueRecord.priority == 'emergency', 1),
        (QueueRecord.priority == 'priority', 2),
        else_=3
    )
    waiting = QueueRecord.query.filter_by(doctor_id=doctor_id, status='waiting').order_by(priority_order, QueueRecord.queue_number).all()
    for i, q in enumerate(waiting):
        q.estimated_wait = i * doctor.consultation_time
    db.session.commit()

@app.route('/')
def home():
    return render_template('home_new.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        role = request.form['role']
        name = request.form['name']
        phone_number = request.form.get('phone_number')
        profile_picture_file = request.files.get('profile_picture')
        profile_picture_filename = None
        
        if User.query.filter_by(username=username).first():
            flash('Username already exists')
            return redirect(url_for('register'))
        if User.query.filter_by(email=email).first():
            flash('Email already exists')
            return redirect(url_for('register'))

        if profile_picture_file and profile_picture_file.filename:
            if not allowed_profile_image(profile_picture_file.filename):
                flash('Please upload a valid image file for the profile picture.')
                return redirect(url_for('register'))
            safe_name = secure_filename(profile_picture_file.filename)
            unique_name = f"{uuid4().hex}_{safe_name}"
            save_path = os.path.join(app.config['PROFILE_PICTURE_UPLOAD_FOLDER'], unique_name)
            profile_picture_file.save(save_path)
            profile_picture_filename = unique_name
        
        user = User(
            username=username,
            email=email,
            role=role,
            name=name,
            phone_number=phone_number,
            profile_picture=profile_picture_filename,
            email_verified=False
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        # Send verification before the new account can be used.
        send_verification_email(user)
        
        if role == 'doctor':
            specialty = request.form['specialty']
            consultation_time = int(request.form.get('consultation_time', 15))
            allows_telemedicine = request.form.get('allows_telemedicine') == 'on'
            doctor = Doctor(
                user_id=user.id,
                specialty=specialty,
                consultation_time=consultation_time,
                allows_telemedicine=allows_telemedicine
            )
            db.session.add(doctor)
            db.session.commit()
        
        flash('Registration successful. Please verify your email before logging in.')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/uploads/profile_pictures/<filename>')
def profile_picture(filename):
    return send_from_directory(app.config['PROFILE_PICTURE_UPLOAD_FOLDER'], filename)

@app.route('/ai_chat', methods=['POST'])
@login_required
def ai_chat():
    if current_user.role != 'patient':
        return jsonify({'error': 'Chat is available for patients only.'}), 403

    message = request.json.get('message', '').strip()
    response = generate_ai_response(message)
    chat_history = session.get('ai_chat_history', [])
    chat_history.append({'user': message, 'assistant': response})
    session['ai_chat_history'] = chat_history[-20:]
    return jsonify({'message': response, 'chat_history': session['ai_chat_history']})

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            if not getattr(user, 'email_verified', False):
                user.email_verified = True
                user.email_verified_at = datetime.utcnow()
                db.session.commit()
            login_user(user)
            token = issue_jwt_token(user)
            response = redirect(url_for('dashboard'))
            response.set_cookie(
                'auth_token',
                token,
                httponly=True,
                secure=not app.debug,
                samesite='Lax',
                max_age=app.config.get('JWT_ACCESS_TOKEN_EXPIRES_MINUTES', 60) * 60,
            )
            return response
        flash('Invalid credentials')
    return render_template('login.html')


@app.route('/verify_email/<token>')
def verify_email(token):
    payload = verify_timed_token(token, 'email-verification')
    if not payload:
        flash('Verification link is invalid or has expired.')
        return redirect(url_for('login'))

    user = User.query.get(payload.get('user_id'))
    if not user or user.email != payload.get('email'):
        flash('Verification link is invalid.')
        return redirect(url_for('login'))

    user.email_verified = True
    user.email_verified_at = datetime.utcnow()
    user.email_verification_token = None
    db.session.commit()
    flash('Email verified successfully. You can now log in.')
    return redirect(url_for('login'))

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email']
        user = User.query.filter_by(email=email).first()
        if user:
            session['reset_user_id'] = user.id
            send_password_reset_email(user)
            flash('Password reset instructions were sent to your email.')
            return redirect(url_for('reset_password'))
        flash('If an account with that email exists, reset instructions have been sent.')
    return render_template('forgot_password.html')

@app.route('/reset_password', methods=['GET', 'POST'])
def reset_password():
    user_id = session.get('reset_user_id')
    if not user_id:
        return redirect(url_for('login'))
    user = User.query.get(user_id)
    if not user:
        flash('Invalid reset request')
        return redirect(url_for('login'))
    if request.method == 'POST':
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        if password != confirm_password:
            flash('Passwords do not match')
            return redirect(url_for('reset_password'))
        user.set_password(password)
        db.session.commit()
        session.pop('reset_user_id', None)
        flash('Password reset successful. Please login.')
        return redirect(url_for('login'))
    return render_template('reset_password.html')


@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password_token(token):
    payload = verify_timed_token(token, 'password-reset')
    if not payload:
        flash('Reset link is invalid or has expired.')
        return redirect(url_for('forgot_password'))

    user = User.query.get(payload.get('user_id'))
    if not user or user.email != payload.get('email'):
        flash('Reset link is invalid.')
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        if password != confirm_password:
            flash('Passwords do not match')
            return redirect(url_for('reset_password_token', token=token))
        user.set_password(password)
        db.session.commit()
        flash('Password reset successful. Please login.')
        return redirect(url_for('login'))

    return render_template('reset_password.html')


@app.route('/api/auth/register', methods=['POST'])
def api_register():
    payload = request.get_json(silent=True) or {}
    required_fields = ('username', 'email', 'password', 'name')
    missing = [field for field in required_fields if not payload.get(field)]
    if missing:
        return jsonify({'error': _('Missing required registration fields.')}), 400

    if User.query.filter_by(username=payload['username']).first() or User.query.filter_by(email=payload['email']).first():
        return jsonify({'error': _('An account with that username or email already exists.')}), 400

    user = User(
        username=payload['username'],
        email=payload['email'],
        role=payload.get('role', 'patient'),
        name=payload['name'],
        phone_number=payload.get('phone_number'),
        email_verified=False,
    )
    user.set_password(payload['password'])
    db.session.add(user)
    db.session.commit()
    verification_link = send_verification_email(user)
    return jsonify({
        'message': _('Registration successful. Please verify your email.'),
        'verification_link': verification_link,
        'user_id': user.id,
    }), 201


@app.route('/api/auth/login', methods=['POST'])
def api_login():
    payload = request.get_json(silent=True) or {}
    username = (payload.get('username') or '').strip()
    password = payload.get('password') or ''
    user = User.query.filter_by(username=username).first()
    if not user or not user.check_password(password):
        return jsonify({'error': _('Invalid credentials.')}), 401
    if not user.email_verified:
        return jsonify({'error': _('Please verify your email before signing in.')}), 403

    token = issue_jwt_token(user)
    return jsonify({
        'access_token': token,
        'token_type': 'Bearer',
        'user': {
            'id': user.id,
            'username': user.username,
            'name': user.name,
            'email': user.email,
            'role': user.role,
        }
    })


@app.route('/api/auth/me')
@jwt_required_api
def api_auth_me():
    user = request.jwt_user
    return jsonify({
        'id': user.id,
        'username': user.username,
        'name': user.name,
        'email': user.email,
        'role': user.role,
        'email_verified': user.email_verified,
    })


@app.route('/newsletter/subscribe', methods=['POST'])
def newsletter_subscribe():
    email = request.form.get('email', '').strip().lower()
    name = request.form.get('name', '').strip() or None
    if not email:
        flash('Email is required for newsletter subscription.')
        return redirect(url_for('home'))
    subscriber = NewsletterSubscriber.query.filter_by(email=email).first()
    if not subscriber:
        subscriber = NewsletterSubscriber(email=email, name=name)
        db.session.add(subscriber)
        db.session.commit()
    flash('You are subscribed to the newsletter.')
    return redirect(url_for('home'))


@app.route('/notifications')
@login_required
def notifications_view():
    if current_user.role not in ('patient', 'doctor', 'admin'):
        return redirect(url_for('dashboard'))
    notifications = get_user_notifications(current_user.id)
    return render_template('notifications.html', notifications=notifications)


@app.route('/notifications/<int:notification_id>/read', methods=['POST'])
@login_required
def mark_notification_read(notification_id):
    notification = Notification.query.filter_by(id=notification_id, recipient_user_id=current_user.id).first_or_404()
    notification.is_read = True
    db.session.commit()
    return redirect(url_for('notifications_view'))


@app.route('/communications')
@login_required
def communications_center():
    if current_user.role not in ('patient', 'doctor', 'admin'):
        return redirect(url_for('dashboard'))

    notifications = get_user_notifications(current_user.id)
    announcements = Announcement.query.filter(
        (Announcement.audience == 'all') | (Announcement.audience == current_user.role)
    ).order_by(Announcement.created_at.desc()).limit(8).all()

    chat_consultations = []
    if current_user.role == 'patient':
        chat_consultations = ChatConsultation.query.filter_by(patient_id=current_user.id).order_by(ChatConsultation.created_at.desc()).limit(6).all()
    elif current_user.role == 'doctor':
        doctor = Doctor.query.filter_by(user_id=current_user.id).first()
        if doctor:
            chat_consultations = ChatConsultation.query.filter(
                (ChatConsultation.doctor_id == doctor.id) | (ChatConsultation.doctor_id.is_(None))
            ).order_by(ChatConsultation.created_at.desc()).limit(6).all()

    support_history = session.get('support_chat_history', [])[-6:]
    ai_history = session.get('ai_chat_history', [])[-6:]

    return render_template(
        'communications_center.html',
        notifications=notifications,
        announcements=announcements,
        chat_consultations=chat_consultations,
        support_history=support_history,
        ai_history=ai_history,
    )


@app.route('/doctor/<int:doctor_id>/reviews', methods=['GET', 'POST'])
@login_required
def doctor_reviews(doctor_id):
    doctor = Doctor.query.get_or_404(doctor_id)
    if request.method == 'POST':
        if current_user.role != 'patient':
            flash('Only patients can leave reviews.')
            return redirect(url_for('doctor_reviews', doctor_id=doctor_id))
        rating = request.form.get('rating', type=int)
        review_text = request.form.get('review', '').strip() or None
        if rating is None or rating < 1 or rating > 5:
            flash('Please choose a rating from 1 to 5.')
            return redirect(url_for('doctor_reviews', doctor_id=doctor_id))
        existing = DoctorReview.query.filter_by(doctor_id=doctor.id, patient_id=current_user.id).first()
        if existing:
            existing.rating = rating
            existing.review = review_text
            existing.updated_at = datetime.utcnow()
        else:
            db.session.add(DoctorReview(doctor_id=doctor.id, patient_id=current_user.id, rating=rating, review=review_text))
        db.session.commit()
        flash('Review submitted successfully.')
        return redirect(url_for('doctor_reviews', doctor_id=doctor_id))

    review_summary = get_latest_doctor_review_summary(doctor.id)
    return render_template('doctor_reviews.html', doctor=doctor, review_summary=review_summary)


@app.route('/api/doctors/<int:doctor_id>/reviews', methods=['GET'])
def api_doctor_reviews(doctor_id):
    doctor = Doctor.query.get_or_404(doctor_id)
    summary = get_latest_doctor_review_summary(doctor.id)
    return jsonify({
        'doctor_id': doctor.id,
        'rating': summary['rating'],
        'count': summary['count'],
        'reviews': [
            {
                'patient_name': review.patient.name,
                'rating': review.rating,
                'review': review.review,
                'created_at': review.created_at.isoformat(),
            }
            for review in summary['reviews']
        ],
    })


@app.route('/medical_reminders', methods=['GET', 'POST'])
@login_required
def medical_reminders():
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        medication_name = request.form.get('medication_name', '').strip()
        dosage_instructions = request.form.get('dosage_instructions', '').strip() or None
        reminder_time_value = request.form.get('reminder_time', '').strip()
        notes = request.form.get('notes', '').strip() or None
        if not medication_name or not reminder_time_value:
            flash('Medication name and reminder time are required.')
            return redirect(url_for('medical_reminders'))
        reminder = MedicationReminder(
            patient_id=current_user.id,
            medication_name=medication_name,
            dosage_instructions=dosage_instructions,
            reminder_time=datetime.fromisoformat(reminder_time_value),
            notes=notes,
        )
        db.session.add(reminder)
        db.session.commit()
        flash('Medication reminder saved.')
        return redirect(url_for('medical_reminders'))
    reminders = MedicationReminder.query.filter_by(patient_id=current_user.id).order_by(MedicationReminder.reminder_time.asc()).all()
    return render_template('medical_reminders.html', reminders=reminders)


@app.route('/admin/announcements', methods=['GET', 'POST'])
@login_required
def admin_announcements():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        message = request.form.get('message', '').strip()
        audience = request.form.get('audience', 'all').strip() or 'all'
        if not title or not message:
            flash('Title and message are required.')
            return redirect(url_for('admin_announcements'))
        db.session.add(Announcement(title=title, message=message, audience=audience, created_by_user_id=current_user.id))
        db.session.commit()
        flash('Announcement published.')
        return redirect(url_for('admin_announcements'))
    announcements = Announcement.query.order_by(Announcement.created_at.desc()).all()
    return render_template('admin_announcements.html', announcements=announcements)

@app.route('/manage_slots', methods=['GET', 'POST'])
@login_required
def manage_slots():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    
    doctors = Doctor.query.all()
    slots = []
    
    if request.method == 'POST':
        doctor_id = request.form['doctor_id']
        date_str = request.form['date']
        start_time_str = request.form['start_time']
        end_time_str = request.form['end_time']
        
        date = datetime.strptime(date_str, '%Y-%m-%d').date()
        start_time = parse_form_time(start_time_str)
        end_time = parse_form_time(end_time_str)
        
        slot = AppointmentSlot(doctor_id=doctor_id, date=date, start_time=start_time, end_time=end_time)
        db.session.add(slot)
        db.session.commit()
        flash('Appointment slot added successfully')
        return redirect(url_for('manage_slots'))
    
    # Get all slots
    slots = AppointmentSlot.query.order_by(AppointmentSlot.date, AppointmentSlot.start_time).all()
    
    return render_template('manage_slots.html', doctors=doctors, slots=slots)

@app.route('/dashboard')
@login_required
def dashboard():
    mark_missed_appointments()
    expire_stale_waiting_patients()
    if current_user.role == 'patient':
        appointments = Appointment.query.filter_by(patient_id=current_user.id).order_by(Appointment.appointment_time.desc()).all()
        queue_records = QueueRecord.query.filter(
            QueueRecord.patient_id == current_user.id,
            QueueRecord.status.in_(['waiting', 'in_consultation'])
        ).all()
        patient_queue = []
        for q in queue_records:
            if q.status == 'waiting':
                position = QueueRecord.query.filter(QueueRecord.doctor_id == q.doctor_id, QueueRecord.status == 'waiting', QueueRecord.queue_number < q.queue_number).count() + 1
            else:
                position = 0
            current_serving = QueueRecord.query.filter_by(doctor_id=q.doctor_id, status='in_consultation').order_by(QueueRecord.queue_number).first()
            patient_queue.append({
                'record': q,
                'position': position,
                'current_serving': current_serving
            })
        notifications = build_patient_notifications(appointments, patient_queue)
        chat_history = session.get('ai_chat_history', [])
        return render_template('patient_dashboard.html', appointments=appointments, patient_queue=patient_queue, notifications=notifications, chat_history=chat_history)
    elif current_user.role == 'doctor':
        doctor = Doctor.query.filter_by(user_id=current_user.id).first()
        if doctor:
            now = datetime.utcnow()
            appointments = Appointment.query.filter_by(doctor_id=doctor.id).order_by(Appointment.appointment_time.desc()).all()
            upcoming_appointments = Appointment.query.filter(
                Appointment.doctor_id == doctor.id,
                Appointment.status == 'scheduled',
                Appointment.appointment_time >= now
            ).order_by(Appointment.appointment_time).all()
            # Get today's appointments
            today = datetime.utcnow().date()
            today_appointments = Appointment.query.filter(
                Appointment.doctor_id == doctor.id,
                Appointment.status == 'scheduled',
                Appointment.appointment_time >= datetime.combine(today, datetime.min.time()),
                Appointment.appointment_time <= datetime.combine(today, datetime.max.time())
            ).all()
            # Order queue by priority first, then queue number
            priority_order = db.case(
                (QueueRecord.priority == 'emergency', 1),
                (QueueRecord.priority == 'priority', 2),
                else_=3
            )
            queue = QueueRecord.query.filter_by(doctor_id=doctor.id, status='waiting').order_by(priority_order, QueueRecord.queue_number).all()
            current_patient = QueueRecord.query.filter_by(doctor_id=doctor.id, status='in_consultation').order_by(QueueRecord.queue_number).first()
            waiting_count = QueueRecord.query.filter_by(doctor_id=doctor.id, status='waiting').count()
            # Get completed consultations for today
            completed_today = QueueRecord.query.filter(
                QueueRecord.doctor_id == doctor.id,
                QueueRecord.status == 'completed',
                QueueRecord.created_at >= today
            ).order_by(QueueRecord.created_at.desc()).all()
            chat_consultations_assigned = ChatConsultation.query.filter_by(doctor_id=doctor.id).order_by(ChatConsultation.created_at.desc()).all()
            unassigned_consultations = ChatConsultation.query.filter(
                ChatConsultation.doctor_id.is_(None),
                ChatConsultation.status == 'waiting_response'
            ).order_by(ChatConsultation.created_at.desc()).all()
            virtual_appointments = Appointment.query.filter(
                Appointment.doctor_id == doctor.id,
                Appointment.consultation_type == 'virtual',
                Appointment.status.in_(['scheduled', 'checked_in'])
            ).order_by(Appointment.appointment_time).all()
            return render_template('doctor_dashboard.html', appointments=appointments, upcoming_appointments=upcoming_appointments, today_appointments=today_appointments, queue=queue, current_patient=current_patient, waiting_count=waiting_count, doctor=doctor, completed_today=completed_today, chat_consultations_assigned=chat_consultations_assigned, unassigned_consultations=unassigned_consultations, virtual_appointments=virtual_appointments)
    elif current_user.role == 'admin':
        users = User.query.all()
        doctors = Doctor.query.all()
        search = request.args.get('search', '').strip()
        status_filter = request.args.get('status', '').strip()
        doctor_filter = request.args.get('doctor_id', type=int)

        appointments_query = Appointment.query.join(User, Appointment.patient_id == User.id)
        if search:
            appointments_query = appointments_query.filter(User.name.ilike(f'%{search}%'))
        if status_filter:
            appointments_query = appointments_query.filter(Appointment.status == status_filter)
        if doctor_filter:
            appointments_query = appointments_query.filter(Appointment.doctor_id == doctor_filter)
        appointments = appointments_query.order_by(Appointment.appointment_time.desc()).all()

        priority_order = db.case(
            (QueueRecord.priority == 'emergency', 1),
            (QueueRecord.priority == 'priority', 2),
            else_=3
        )
        active_queue = QueueRecord.query.filter(
            QueueRecord.status.in_(['waiting', 'in_consultation'])
        ).order_by(QueueRecord.doctor_id, priority_order, QueueRecord.queue_number).all()
        today = datetime.utcnow().date()
        today_appointments = Appointment.query.filter(
            Appointment.appointment_time >= datetime.combine(today, datetime.min.time()),
            Appointment.appointment_time <= datetime.combine(today, datetime.max.time())
        ).count()
        total_bookings = Appointment.query.count()
        return render_template('admin_dashboard.html', users=users, doctors=doctors, appointments=appointments, active_queue=active_queue, today_appointments=today_appointments, total_bookings=total_bookings, search=search, status_filter=status_filter, doctor_filter=doctor_filter)
    return redirect(url_for('home'))

@app.route('/reminders')
@login_required
def reminders():
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))

    appointments = Appointment.query.filter_by(patient_id=current_user.id).order_by(Appointment.appointment_time.desc()).all()
    queue_records = QueueRecord.query.filter(
        QueueRecord.patient_id == current_user.id,
        QueueRecord.status.in_(['waiting', 'in_consultation'])
    ).all()
    patient_queue = []
    for q in queue_records:
        if q.status == 'waiting':
            position = QueueRecord.query.filter(QueueRecord.doctor_id == q.doctor_id, QueueRecord.status == 'waiting', QueueRecord.queue_number < q.queue_number).count() + 1
        else:
            position = 0
        current_serving = QueueRecord.query.filter_by(doctor_id=q.doctor_id, status='in_consultation').order_by(QueueRecord.queue_number).first()
        patient_queue.append({
            'record': q,
            'position': position,
            'current_serving': current_serving
        })

    reminders = build_patient_reminders(appointments, patient_queue)
    upcoming_appointments = [appointment for appointment in appointments if appointment.status == 'scheduled']
    upcoming_appointments = sorted(upcoming_appointments, key=lambda appointment: appointment.appointment_time)[:5]

    return render_template(
        'reminders.html',
        reminders=reminders,
        upcoming_appointments=upcoming_appointments,
        patient_queue=patient_queue
    )

@app.route('/symptom_checker', methods=['GET', 'POST'])
def symptom_checker():
    result = None
    symptoms = ''
    duration = ''
    age_group = ''
    fever = False
    breathing = False
    chest_pain = False

    if request.method == 'POST':
        symptoms = request.form.get('symptoms', '').strip()
        duration = request.form.get('duration', '').strip()
        age_group = request.form.get('age_group', '').strip()
        fever = request.form.get('fever') == 'on'
        breathing = request.form.get('breathing') == 'on'
        chest_pain = request.form.get('chest_pain') == 'on'
        # include simple patient context when available
        patient_allergies = ''
        patient_age = None
        try:
            if current_user and current_user.is_authenticated:
                patient_allergies = getattr(current_user, 'allergies', '') or ''
                dob = getattr(current_user, 'date_of_birth', None)
                if dob:
                    try:
                        patient_age = int((datetime.utcnow().date() - dob).days // 365)
                    except Exception:
                        patient_age = None
        except Exception:
            patient_allergies = ''

        result = analyze_symptoms(symptoms, duration, age_group, fever=fever, breathing=breathing, chest_pain=chest_pain, patient_allergies=patient_allergies, patient_age=patient_age)

    return render_template(
        'symptom_checker.html',
        result=result,
        symptoms=symptoms,
        duration=duration,
        age_group=age_group,
        fever=fever,
        breathing=breathing,
        chest_pain=chest_pain
    )

@app.route('/support')
def support():
    support_history = session.get('support_chat_history', [])
    return render_template('support.html', support_chat_history=support_history)

@app.route('/support_chat_api', methods=['POST'])
def support_chat_api():
    message = request.json.get('message', '').strip()
    response = generate_support_response(message)
    support_history = session.get('support_chat_history', [])
    support_history.append({'user': message, 'assistant': response})
    session['support_chat_history'] = support_history[-20:]
    return jsonify({'message': response, 'chat_history': session['support_chat_history']})

@app.route('/medical_history')
@login_required
def medical_history():
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))

    appointments = Appointment.query.filter_by(patient_id=current_user.id).order_by(Appointment.appointment_time.desc()).all()
    queue_records = QueueRecord.query.filter_by(patient_id=current_user.id).order_by(QueueRecord.created_at.desc()).all()

    completed_appointments = [appointment for appointment in appointments if appointment.status == 'completed']
    upcoming_appointments = [appointment for appointment in appointments if appointment.status == 'scheduled']
    recent_appointment = appointments[0] if appointments else None
    last_completed = completed_appointments[0] if completed_appointments else None

    return render_template(
        'medical_history.html',
        appointments=appointments,
        queue_records=queue_records,
        completed_appointments=completed_appointments,
        upcoming_appointments=upcoming_appointments,
        recent_appointment=recent_appointment,
        last_completed=last_completed
    )

@app.route('/family_members', methods=['GET', 'POST'])
@login_required
def family_members():
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        name = request.form['name'].strip()
        relationship = request.form['relationship'].strip()
        phone_number = request.form.get('phone_number', '').strip() or None
        date_of_birth = request.form.get('date_of_birth', '').strip()
        gender = request.form.get('gender', '').strip() or None
        notes = request.form.get('notes', '').strip() or None

        if not name or not relationship:
            flash('Name and relationship are required.')
            return redirect(url_for('family_members'))

        family_member = FamilyMember(
            patient_id=current_user.id,
            name=name,
            relationship=relationship,
            phone_number=phone_number,
            date_of_birth=datetime.strptime(date_of_birth, '%Y-%m-%d').date() if date_of_birth else None,
            gender=gender,
            notes=notes
        )
        db.session.add(family_member)
        db.session.commit()
        flash('Family member added successfully.')
        return redirect(url_for('family_members'))

    members = get_patient_family_members(current_user.id)
    return render_template('family_members.html', family_members=members)

@app.route('/family_members/<int:family_member_id>/delete', methods=['POST'])
@login_required
def delete_family_member(family_member_id):
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))

    family_member = FamilyMember.query.filter_by(id=family_member_id, patient_id=current_user.id).first_or_404()
    active_appointment = Appointment.query.filter_by(family_member_id=family_member.id).filter(Appointment.status.in_(['scheduled', 'checked_in'])).first()
    if active_appointment:
        flash('This family member is linked to an active appointment and cannot be removed yet.')
        return redirect(url_for('family_members'))

    db.session.delete(family_member)
    db.session.commit()
    flash('Family member removed successfully.')
    return redirect(url_for('family_members'))

@app.route('/emergency_sos', methods=['GET', 'POST'])
@login_required
def emergency_sos():
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        location_text = request.form.get('location_text', '').strip() or None
        latitude = request.form.get('latitude', type=float)
        longitude = request.form.get('longitude', type=float)
        notes = request.form.get('notes', '').strip() or None

        alert = EmergencyAlert(
            patient_id=current_user.id,
            location_text=location_text,
            latitude=latitude,
            longitude=longitude,
            contacted_number=current_user.emergency_contact,
            notes=notes
        )
        db.session.add(alert)
        db.session.commit()
        flash('Emergency alert recorded. Call the ambulance line now and share your location with contacts.')
        return redirect(url_for('emergency_sos'))

    recent_alerts = EmergencyAlert.query.filter_by(patient_id=current_user.id).order_by(EmergencyAlert.created_at.desc()).limit(5).all()
    return render_template('emergency_sos.html', recent_alerts=recent_alerts)

@app.route('/records', methods=['GET', 'POST'])
@login_required
def health_records():
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        title = request.form['title'].strip()
        record_type = request.form['record_type'].strip()
        description = request.form.get('description', '').strip() or None
        shared_with_doctor_id = request.form.get('shared_with_doctor_id', type=int)
        upload = request.files.get('record_file')

        if not title or not record_type or not upload or not upload.filename:
            flash('Title, record type, and file are required.')
            return redirect(url_for('health_records'))
        if not allowed_health_record_file(upload.filename):
            flash('Please upload a supported document, image, or PDF.')
            return redirect(url_for('health_records'))

        doctor = None
        if shared_with_doctor_id:
            doctor = Doctor.query.filter_by(id=shared_with_doctor_id).first()
            if not doctor:
                flash('Selected doctor was not found.')
                return redirect(url_for('health_records'))

        safe_name = secure_filename(upload.filename)
        stored_name = f"{uuid4().hex}_{safe_name}"
        upload_path = os.path.join(app.config['HEALTH_RECORD_UPLOAD_FOLDER'], stored_name)
        try:
            # Save file to disk
            upload.save(upload_path)
        except Exception as e:
            flash(f'Failed to save uploaded file: {str(e)}')
            return redirect(url_for('health_records'))

        try:
            record = HealthRecord(
                patient_id=current_user.id,
                shared_with_doctor_id=doctor.id if doctor else None,
                title=title,
                record_type=record_type,
                description=description,
                file_name=stored_name,
                original_filename=safe_name
            )
            db.session.add(record)
            db.session.commit()
        except Exception as e:
            # Attempt to remove the saved file if DB commit fails
            try:
                if os.path.exists(upload_path):
                    os.remove(upload_path)
            except Exception:
                pass
            flash(f'Failed to save record to database: {str(e)}')
            return redirect(url_for('health_records'))

        flash('Health record uploaded successfully.')
        return redirect(url_for('health_records'))

    records = HealthRecord.query.filter_by(patient_id=current_user.id).order_by(HealthRecord.created_at.desc()).all()
    doctors = Doctor.query.join(User).order_by(User.name).all()
    return render_template('records.html', records=records, doctors=doctors)

@app.route('/records/<int:record_id>/download')
@login_required
def download_health_record(record_id):
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))

    record = HealthRecord.query.filter_by(id=record_id, patient_id=current_user.id).first_or_404()
    return send_from_directory(
        app.config['HEALTH_RECORD_UPLOAD_FOLDER'],
        record.file_name,
        as_attachment=True,
        download_name=record.original_filename
    )

@app.route('/lab_tests', methods=['GET', 'POST'])
@login_required
def lab_tests():
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        test_name = request.form['test_name'].strip()
        preferred_date = request.form.get('preferred_date', '').strip()
        notes = request.form.get('notes', '').strip() or None

        if not test_name:
            flash('Please choose a lab test to book.')
            return redirect(url_for('lab_tests'))

        booking = LabTestBooking(
            patient_id=current_user.id,
            test_name=test_name,
            preferred_date=datetime.strptime(preferred_date, '%Y-%m-%d').date() if preferred_date else None,
            notes=notes
        )
        db.session.add(booking)
        db.session.commit()
        flash('Lab test request submitted successfully.')
        return redirect(url_for('lab_tests'))

    bookings = LabTestBooking.query.filter_by(patient_id=current_user.id).order_by(LabTestBooking.created_at.desc()).all()
    return render_template('lab_tests.html', bookings=bookings)

@app.route('/insurance', methods=['GET', 'POST'])
@login_required
def insurance():
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))

    insurance_profile = get_patient_insurance_profile(current_user.id)
    if request.method == 'POST':
        provider_name = request.form['provider_name'].strip()
        policy_number = request.form['policy_number'].strip()
        member_id = request.form.get('member_id', '').strip() or None
        coverage_level = request.form.get('coverage_level', '').strip() or None
        coverage_notes = request.form.get('coverage_notes', '').strip() or None
        expiry_date = request.form.get('expiry_date', '').strip()

        if not provider_name or not policy_number:
            flash('Provider name and policy number are required.')
            return redirect(url_for('insurance'))

        if not insurance_profile:
            insurance_profile = InsuranceProfile(
                patient_id=current_user.id,
                digital_card_number=uuid4().hex[:12].upper()
            )

        insurance_profile.provider_name = provider_name
        insurance_profile.policy_number = policy_number
        insurance_profile.member_id = member_id
        insurance_profile.coverage_level = coverage_level
        insurance_profile.coverage_notes = coverage_notes
        insurance_profile.expiry_date = datetime.strptime(expiry_date, '%Y-%m-%d').date() if expiry_date else None
        db.session.add(insurance_profile)
        db.session.commit()
        flash('Insurance profile saved successfully.')
        return redirect(url_for('insurance'))

    return render_template('insurance.html', insurance_profile=insurance_profile)

@app.route('/patient_tools')
@login_required
def patient_tools():
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))
    return render_template('patient_tools.html')

@app.route('/wellness_dashboard', methods=['GET', 'POST'])
@login_required
def wellness_dashboard():
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        weight_kg = request.form.get('weight_kg', type=float)
        height_cm = request.form.get('height_cm', type=float)
        blood_pressure_systolic = request.form.get('blood_pressure_systolic', type=int)
        blood_pressure_diastolic = request.form.get('blood_pressure_diastolic', type=int)
        blood_sugar_mg_dl = request.form.get('blood_sugar_mg_dl', type=float)
        notes = request.form.get('notes', '').strip() or None
        bmi = calculate_bmi(weight_kg, height_cm)

        metric = WellnessMetric(
            patient_id=current_user.id,
            weight_kg=weight_kg,
            height_cm=height_cm,
            blood_pressure_systolic=blood_pressure_systolic,
            blood_pressure_diastolic=blood_pressure_diastolic,
            blood_sugar_mg_dl=blood_sugar_mg_dl,
            bmi=bmi,
            notes=notes
        )
        db.session.add(metric)
        db.session.commit()
        flash('Wellness measurement saved successfully.')
        return redirect(url_for('wellness_dashboard'))

    metrics = WellnessMetric.query.filter_by(patient_id=current_user.id).order_by(WellnessMetric.measured_at.desc()).all()
    latest_metric = metrics[0] if metrics else None
    recommendations = get_wellness_recommendations(latest_metric)
    return render_template('wellness_dashboard.html', metrics=metrics, latest_metric=latest_metric, recommendations=recommendations)

@app.route('/blood_donation', methods=['GET', 'POST'])
@login_required
def blood_donation():
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        action = request.form.get('action', 'request')
        if action == 'register_donor':
            donor = BloodDonor(
                name=request.form['name'].strip(),
                blood_group=request.form['blood_group'].strip().upper(),
                phone_number=request.form.get('phone_number', '').strip() or None,
                city=request.form.get('city', '').strip() or None,
                notes=request.form.get('notes', '').strip() or None,
                available=request.form.get('available') == 'on'
            )
            db.session.add(donor)
            db.session.commit()
            flash('Blood donor profile saved successfully.')
            return redirect(url_for('blood_donation'))

        blood_group = request.form['blood_group'].strip().upper()
        hospital_name = request.form['hospital_name'].strip()
        urgency_level = request.form.get('urgency_level', 'normal')
        message = request.form.get('message', '').strip() or None
        request_entry = BloodDonationRequest(
            patient_id=current_user.id,
            blood_group=blood_group,
            hospital_name=hospital_name,
            urgency_level=urgency_level,
            message=message
        )
        db.session.add(request_entry)
        db.session.commit()
        flash('Blood request submitted. Matching donors are listed on the page.')
        return redirect(url_for('blood_donation', blood_group=blood_group))

    blood_group = request.args.get('blood_group', '').strip().upper()
    city = request.args.get('city', '').strip() or None
    donors = get_blood_donor_matches(blood_group, city) if blood_group else BloodDonor.query.filter_by(available=True).order_by(BloodDonor.created_at.desc()).limit(10).all()
    requests_log = BloodDonationRequest.query.order_by(BloodDonationRequest.created_at.desc()).limit(8).all()
    return render_template('blood_donation.html', donors=donors, requests_log=requests_log, blood_group=blood_group, city=city)

@app.route('/hospital_navigation')
@login_required
def hospital_navigation():
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))
    departments = [
        {'name': 'Emergency', 'description': '24/7 urgent care and ambulance arrival point'},
        {'name': 'Outpatient Clinic', 'description': 'General consults and follow-up appointments'},
        {'name': 'Laboratory', 'description': 'Blood tests, diagnostics, and sample collection'},
        {'name': 'Pharmacy', 'description': 'Prescriptions, refills, and medication counseling'},
        {'name': 'Radiology', 'description': 'Imaging and scan services'},
        {'name': 'Maternity', 'description': 'Prenatal and delivery services'},
    ]
    return render_template('hospital_navigation.html', departments=departments)

@app.route('/language_support', methods=['GET', 'POST'])
@login_required
def language_support():
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))

    preference = get_language_preference(current_user.id)
    if request.method == 'POST':
        preferred_language = normalize_locale(request.form.get('preferred_language', 'en'))
        voice_assistance_enabled = request.form.get('voice_assistance_enabled') == 'on'
        if not preference:
            preference = LanguagePreference(patient_id=current_user.id, preferred_language=preferred_language, voice_assistance_enabled=voice_assistance_enabled)
        else:
            preference.preferred_language = preferred_language
            preference.voice_assistance_enabled = voice_assistance_enabled
        session['lang'] = preferred_language
        db.session.add(preference)
        db.session.commit()
        flash('Language preference saved successfully.')
        response = redirect(url_for('language_support'))
        response.set_cookie('lang', preferred_language, max_age=90*24*60*60)
        return response

    return render_template('language_support.html', preference=preference, languages=LANGUAGE_OPTIONS)

@app.route('/vaccinations', methods=['GET', 'POST'])
@login_required
def vaccinations():
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        vaccine_name = request.form['vaccine_name'].strip()
        dose_number = request.form.get('dose_number', '').strip() or None
        vaccination_date = request.form.get('vaccination_date', '').strip()
        next_due_date = request.form.get('next_due_date', '').strip()
        notes = request.form.get('notes', '').strip() or None

        if not vaccine_name:
            flash('Vaccine name is required.')
            return redirect(url_for('vaccinations'))

        record = VaccinationRecord(
            patient_id=current_user.id,
            vaccine_name=vaccine_name,
            dose_number=dose_number,
            vaccination_date=datetime.strptime(vaccination_date, '%Y-%m-%d').date() if vaccination_date else None,
            next_due_date=datetime.strptime(next_due_date, '%Y-%m-%d').date() if next_due_date else None,
            notes=notes
        )
        db.session.add(record)
        db.session.commit()
        flash('Vaccination record saved successfully.')
        return redirect(url_for('vaccinations'))

    records = VaccinationRecord.query.filter_by(patient_id=current_user.id).order_by(VaccinationRecord.created_at.desc()).all()
    return render_template('vaccinations.html', records=records)

@app.route('/recommendations')
@login_required
def recommendations():
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))

    latest_metric = get_latest_wellness_metric(current_user.id)
    recommendations = get_wellness_recommendations(latest_metric)
    latest_vaccinations = VaccinationRecord.query.filter_by(patient_id=current_user.id).order_by(VaccinationRecord.created_at.desc()).limit(3).all()
    suggestions = []
    if current_user.date_of_birth:
        age = (datetime.utcnow().date() - current_user.date_of_birth).days // 365
        if age >= 60:
            suggestions.append('Schedule regular wellness reviews and keep caregivers informed about new appointments.')
        elif age >= 40:
            suggestions.append('Track blood pressure and blood sugar routinely for early prevention.')
    suggestions.append('Book follow-ups early if you have active symptoms or recurring concerns.')
    return render_template('recommendations.html', recommendations=recommendations, suggestions=suggestions, latest_metric=latest_metric, latest_vaccinations=latest_vaccinations)


@app.route('/ai_hub')
@login_required
def ai_hub():
    latest_metric = get_latest_wellness_metric(current_user.id) if current_user.role == 'patient' else None
    recommendations_list = get_wellness_recommendations(latest_metric) if latest_metric else []
    queue_records = []
    if current_user.role == 'patient':
        queue_records = QueueRecord.query.filter_by(patient_id=current_user.id).order_by(QueueRecord.created_at.desc()).all()
    doctor_predictions = []
    if current_user.role == 'patient':
        doctors = Doctor.query.join(User).filter(User.role == 'doctor').limit(6).all()
        for doctor in doctors:
            waiting_count = QueueRecord.query.filter_by(doctor_id=doctor.id, status='waiting').count()
            availability_calendar = get_doctor_availability_calendar(doctor.id)
            doctor_predictions.append({
                'doctor': doctor,
                'estimated_wait': waiting_count * (doctor.consultation_time or 15),
                'next_slot': get_next_available_slot_label(availability_calendar),
                'telemedicine': doctor.allows_telemedicine,
            })
    return render_template(
        'ai_hub.html',
        latest_metric=latest_metric,
        recommendations=recommendations_list,
        queue_records=queue_records,
        doctor_predictions=doctor_predictions,
    )

@app.route('/telemedicine_call/<int:appointment_id>', methods=['GET', 'POST'])
@login_required
def telemedicine_call(appointment_id):
    if current_user.role == 'patient':
        appointment = Appointment.query.filter_by(id=appointment_id, patient_id=current_user.id).first_or_404()
    elif current_user.role == 'doctor':
        doctor = Doctor.query.filter_by(user_id=current_user.id).first()
        if not doctor:
            return redirect(url_for('dashboard'))
        appointment = Appointment.query.filter_by(id=appointment_id, doctor_id=doctor.id).first_or_404()
    else:
        return redirect(url_for('dashboard'))

    if appointment.consultation_type != 'virtual':
        flash('This appointment is not marked for telemedicine.')
        return redirect(url_for('appointment_details', appointment_id=appointment_id))

    session_entry = TelemedicineSession.query.filter_by(appointment_id=appointment.id).first()
    if request.method == 'POST':
        notes = request.form.get('notes', '').strip() or None
        attachment = request.files.get('attachment')
        if attachment and attachment.filename:
            if not allowed_health_record_file(attachment.filename):
                flash('Please upload a supported file or image.')
                return redirect(url_for('telemedicine_call', appointment_id=appointment.id))
            safe_name = secure_filename(attachment.filename)
            stored_name = f"{uuid4().hex}_{safe_name}"
            attachment_path = os.path.join(app.config['HEALTH_RECORD_UPLOAD_FOLDER'], stored_name)
            attachment.save(attachment_path)
            db.session.add(HealthRecord(
                patient_id=current_user.id,
                shared_with_doctor_id=appointment.doctor_id,
                title=f'Telemedicine share - {safe_name}',
                record_type='Telemedicine Share',
                description=notes,
                file_name=stored_name,
                original_filename=safe_name
            ))
        if not session_entry:
            session_entry = TelemedicineSession(appointment_id=appointment.id, meeting_code=uuid4().hex[:10].upper(), notes=notes)
        else:
            session_entry.notes = notes
        db.session.add(session_entry)
        db.session.commit()
        flash('Telemedicine session updated successfully.')
        return redirect(url_for('telemedicine_call', appointment_id=appointment.id))

    if not session_entry:
        session_entry = TelemedicineSession(appointment_id=appointment.id, meeting_code=uuid4().hex[:10].upper())
        db.session.add(session_entry)
        db.session.commit()
    return render_template('telemedicine_call.html', appointment=appointment, session_entry=session_entry)

@app.route('/doctor_availability_predictor')
@login_required
def doctor_availability_predictor():
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))

    doctors = Doctor.query.all()
    predictions = []
    for doctor in doctors:
        waiting_count = QueueRecord.query.filter_by(doctor_id=doctor.id, status='waiting').count()
        scheduled_count = Appointment.query.filter(
            Appointment.doctor_id == doctor.id,
            Appointment.status == 'scheduled',
            Appointment.appointment_time >= datetime.utcnow()
        ).count()
        estimated_wait = waiting_count * (doctor.consultation_time or 15)
        availability_calendar = get_doctor_availability_calendar(doctor.id)
        predictions.append({
            'doctor': doctor,
            'estimated_wait': estimated_wait,
            'busy_level': 'High' if estimated_wait >= 60 else 'Medium' if estimated_wait >= 30 else 'Low',
            'next_slot': get_next_available_slot_label(availability_calendar),
            'telemedicine': doctor.allows_telemedicine,
            'scheduled_count': scheduled_count
        })
    predictions.sort(key=lambda item: (item['estimated_wait'], -item['telemedicine']))
    return render_template('doctor_availability_predictor.html', predictions=predictions[:10])

@app.route('/forum', methods=['GET', 'POST'])
@login_required
def forum():
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        title = request.form['title'].strip()
        content = request.form['content'].strip()
        if not title or not content:
            flash('Title and message are required.')
            return redirect(url_for('forum'))
        post = ForumPost(patient_id=current_user.id, title=title, content=content)
        db.session.add(post)
        db.session.commit()
        flash('Forum post published successfully.')
        return redirect(url_for('forum'))

    posts = ForumPost.query.order_by(ForumPost.created_at.desc()).all()
    return render_template('forum.html', posts=posts)

@app.route('/forum/<int:post_id>/reply', methods=['POST'])
@login_required
def forum_reply(post_id):
    if current_user.role not in ('patient', 'doctor', 'admin'):
        return redirect(url_for('dashboard'))

    post = ForumPost.query.get_or_404(post_id)
    content = request.form['content'].strip()
    if content:
        reply = ForumReply(post_id=post.id, user_id=current_user.id, content=content)
        db.session.add(reply)
        db.session.commit()
        flash('Reply added.')
    return redirect(url_for('forum'))

@app.route('/wearables', methods=['GET', 'POST'])
@login_required
def wearables():
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        snapshot = WearableSnapshot(
            patient_id=current_user.id,
            device_name=request.form['device_name'].strip(),
            heart_rate=request.form.get('heart_rate', type=int),
            steps=request.form.get('steps', type=int),
            sleep_hours=request.form.get('sleep_hours', type=float)
        )
        db.session.add(snapshot)
        db.session.commit()
        flash('Wearable data synced successfully.')
        return redirect(url_for('wearables'))

    snapshots = WearableSnapshot.query.filter_by(patient_id=current_user.id).order_by(WearableSnapshot.recorded_at.desc()).all()
    return render_template('wearables.html', snapshots=snapshots)

@app.route('/pharmacy', methods=['GET', 'POST'])
@login_required
def pharmacy():
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        medicine_id = request.form.get('medicine_id', type=int)
        quantity = request.form.get('quantity', 1, type=int)
        delivery_address = request.form.get('delivery_address', '').strip()
        notes = request.form.get('notes', '').strip() or None
        prescription_file = request.files.get('prescription')

        medicine = PharmacyMedicine.query.get(medicine_id)
        if not medicine:
            flash('Medicine not found.')
            return redirect(url_for('pharmacy'))

        if medicine.requires_prescription and not prescription_file:
            flash('This medicine requires a prescription.')
            return redirect(url_for('pharmacy'))

        total_price = medicine.price * quantity
        prescription_filename = None
        if prescription_file and prescription_file.filename:
            if not allowed_health_record_file(prescription_file.filename):
                flash('Invalid prescription file format.')
                return redirect(url_for('pharmacy'))
            safe_name = secure_filename(prescription_file.filename)
            stored_name = f"{uuid4().hex}_{safe_name}"
            prescription_path = os.path.join(app.config['HEALTH_RECORD_UPLOAD_FOLDER'], stored_name)
            prescription_file.save(prescription_path)
            prescription_filename = stored_name

        order = PharmacyOrder(
            patient_id=current_user.id,
            medicine_id=medicine_id,
            quantity=quantity,
            delivery_address=delivery_address or current_user.phone_number,
            total_price=total_price,
            notes=notes,
            prescription_file=prescription_filename,
            status='pending'
        )
        db.session.add(order)
        db.session.commit()
        # decrement stock if tracked
        try:
            if hasattr(medicine, 'stock_quantity'):
                if medicine.stock_quantity is None:
                    medicine.stock_quantity = 0
                medicine.stock_quantity = max(0, medicine.stock_quantity - quantity)
                medicine.in_stock = medicine.stock_quantity > 0
                db.session.commit()
        except Exception:
            pass
        flash(f'Order placed successfully. Total: GHS {total_price:.2f}')
        return redirect(url_for('pharmacy'))

    medicines = PharmacyMedicine.query.filter_by(in_stock=True).all()
    orders = PharmacyOrder.query.filter_by(patient_id=current_user.id).order_by(PharmacyOrder.created_at.desc()).all()
    return render_template('pharmacy.html', medicines=medicines, orders=orders)


@app.route('/admin/pharmacy', methods=['GET', 'POST'])
@login_required
def admin_pharmacy():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        price = request.form.get('price', type=float)
        dosage = request.form.get('dosage', '').strip() or None
        requires_prescription = request.form.get('requires_prescription') == '1'
        stock_quantity = request.form.get('stock_quantity', type=int) or 0
        if not name or price is None:
            flash('Name and price are required.')
            return redirect(url_for('admin_pharmacy'))
        med = PharmacyMedicine(name=name, price=price, dosage=dosage, requires_prescription=requires_prescription, stock_quantity=stock_quantity, in_stock=(stock_quantity>0))
        db.session.add(med)
        db.session.commit()
        flash('Medicine added.')
        return redirect(url_for('admin_pharmacy'))

    medicines = PharmacyMedicine.query.order_by(PharmacyMedicine.name).all()
    return render_template('admin_pharmacy.html', medicines=medicines)


@app.route('/admin/pharmacy/<int:med_id>/restock', methods=['POST'])
@login_required
def admin_pharmacy_restock(med_id):
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    med = PharmacyMedicine.query.get_or_404(med_id)
    add_qty = request.form.get('add_qty', type=int) or 0
    med.stock_quantity = (med.stock_quantity or 0) + add_qty
    med.in_stock = med.stock_quantity > 0
    db.session.commit()
    flash(f'Restocked {add_qty} units for {med.name}.')
    return redirect(url_for('admin_pharmacy'))


@app.route('/admin/pharmacy/<int:med_id>/toggle')
@login_required
def admin_pharmacy_toggle(med_id):
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    med = PharmacyMedicine.query.get_or_404(med_id)
    med.in_stock = not bool(med.in_stock)
    db.session.commit()
    flash('Toggled availability.')
    return redirect(url_for('admin_pharmacy'))

@app.route('/chat_consultation', methods=['GET', 'POST'])
@login_required
def chat_consultation():
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        topic = request.form.get('topic', '').strip()
        doctor_id = request.form.get('doctor_id', type=int) or None
        if not topic:
            flash('Please provide a topic for consultation.')
            return redirect(url_for('chat_consultation'))

        consultation = ChatConsultation(
            patient_id=current_user.id,
            doctor_id=doctor_id,
            topic=topic,
            status='waiting_response'
        )
        db.session.add(consultation)
        db.session.commit()
        flash('Chat consultation created. A doctor will respond soon.')
        return redirect(url_for('chat_consultation'))

    consultations = ChatConsultation.query.filter_by(patient_id=current_user.id).order_by(ChatConsultation.created_at.desc()).all()
    doctors = Doctor.query.all()
    return render_template('chat_consultation.html', consultations=consultations, doctors=doctors)

@app.route('/chat_consultation/<int:consultation_id>', methods=['GET', 'POST'])
@login_required
def view_chat_consultation(consultation_id):
    consultation = ChatConsultation.query.get_or_404(consultation_id)
    if current_user.role == 'patient' and consultation.patient_id != current_user.id:
        return redirect(url_for('dashboard'))
    if current_user.role == 'doctor':
        doctor = get_or_create_doctor_profile()
        if not doctor:
            return redirect(url_for('dashboard'))
        if consultation.doctor_id is None and consultation.status == 'waiting_response':
            consultation.doctor_id = doctor.id
            consultation.status = 'active'
            consultation.updated_at = datetime.utcnow()
            db.session.commit()
        elif consultation.doctor_id is not None and consultation.doctor_id != doctor.id:
            return redirect(url_for('dashboard'))
    if current_user.role not in ('patient', 'doctor'):
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        attachment = request.files.get('attachment')
        attachment_filename = None
        if attachment and attachment.filename:
            if not allowed_health_record_file(attachment.filename):
                flash('Invalid attachment format.')
                return redirect(url_for('view_chat_consultation', consultation_id=consultation.id))
            safe_name = secure_filename(attachment.filename)
            stored_name = f"{uuid4().hex}_{safe_name}"
            attach_path = os.path.join(app.config['CHAT_UPLOAD_FOLDER'], stored_name)
            try:
                attachment.save(attach_path)
                attachment_filename = stored_name
            except Exception as e:
                flash('Failed to save attachment.')
                return redirect(url_for('view_chat_consultation', consultation_id=consultation.id))

        if message or attachment_filename:
            chat_msg = ChatMessage(
                consultation_id=consultation.id,
                sender_id=current_user.id,
                message=message or '',
                attachment_filename=attachment_filename
            )
            db.session.add(chat_msg)
            consultation.updated_at = datetime.utcnow()
            db.session.commit()
            return redirect(url_for('view_chat_consultation', consultation_id=consultation.id))

    messages = ChatMessage.query.filter_by(consultation_id=consultation.id).order_by(ChatMessage.created_at).all()
    return render_template('view_chat_consultation.html', consultation=consultation, messages=messages)


if socketio_available:
    @socketio.on('join_consultation')
    def handle_join(data):
        consultation_id = data.get('consultation_id')
        room = f'consultation_{consultation_id}'
        join_room(room)

    @socketio.on('leave_consultation')
    def handle_leave(data):
        consultation_id = data.get('consultation_id')
        room = f'consultation_{consultation_id}'
        leave_room(room)

    @socketio.on('send_consultation_message')
    def handle_send_consultation_message(data):
        consultation_id = data.get('consultation_id')
        sender_id = data.get('sender_id')
        message_text = data.get('message', '').strip()
        if not consultation_id or not sender_id or (not message_text):
            return
        # store message in DB
        try:
            chat_msg = ChatMessage(consultation_id=consultation_id, sender_id=sender_id, message=message_text)
            db.session.add(chat_msg)
            db.session.commit()
            out = {
                'id': chat_msg.id,
                'consultation_id': consultation_id,
                'sender_id': sender_id,
                'message': message_text,
                'created_at': chat_msg.created_at.strftime('%Y-%m-%d %H:%M:%S')
            }
            room = f'consultation_{consultation_id}'
            emit('new_consultation_message', out, room=room)
        except Exception:
            db.session.rollback()


@app.route('/chat_attachment/<int:message_id>/download')
@login_required
def download_chat_attachment(message_id):
    msg = ChatMessage.query.get_or_404(message_id)
    consultation = ChatConsultation.query.get(msg.consultation_id)
    # permission check
    if current_user.role == 'patient' and consultation.patient_id != current_user.id:
        return redirect(url_for('dashboard'))
    if current_user.role == 'doctor':
        doctor = get_or_create_doctor_profile()
        if not doctor or consultation.doctor_id != doctor.id:
            return redirect(url_for('dashboard'))
    if not msg.attachment_filename:
        flash('No attachment found.')
        return redirect(url_for('view_chat_consultation', consultation_id=consultation.id))
    return send_from_directory(app.config['CHAT_UPLOAD_FOLDER'], msg.attachment_filename, as_attachment=True)

@app.route('/billing')
@login_required
def billing():
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))

    appointments = Appointment.query.filter_by(patient_id=current_user.id).order_by(Appointment.appointment_time.desc()).all()
    billing_items = [appointment for appointment in appointments if appointment.amount_due > 0 or appointment.payment_status != 'pending']
    total_due = sum(appointment.amount_due for appointment in billing_items if appointment.payment_status != 'paid')
    total_paid = sum(appointment.amount_due for appointment in billing_items if appointment.payment_status == 'paid')
    pending_count = sum(1 for appointment in billing_items if appointment.payment_status != 'paid')

    return render_template(
        'billing.html',
        billing_items=billing_items,
        total_due=total_due,
        total_paid=total_paid,
        pending_count=pending_count
    )

@app.route('/receipt/<int:appointment_id>')
@login_required
def receipt(appointment_id):
    appointment = Appointment.query.get_or_404(appointment_id)
    if current_user.role != 'patient' or appointment.patient_id != current_user.id:
        flash('Access denied')
        return redirect(url_for('dashboard'))

    return render_template('receipt.html', appointment=appointment)

@app.route('/doctors')
def doctors_directory():
    specialty = request.args.get('specialty', '')
    search_query = request.args.get('search', '')
    telemedicine_filter = request.args.get('telemedicine', '')
    availability_filter = request.args.get('availability', '')
    max_fee = request.args.get('max_fee', type=float)
    doctors_query = Doctor.query.join(User)
    
    if specialty:
        doctors_query = doctors_query.filter(Doctor.specialty.ilike(f'%{specialty}%'))
    if search_query:
        doctors_query = doctors_query.filter(User.name.ilike(f'%{search_query}%'))
    if telemedicine_filter == 'yes':
        doctors_query = doctors_query.filter(Doctor.allows_telemedicine.is_(True))
    elif telemedicine_filter == 'no':
        doctors_query = doctors_query.filter(Doctor.allows_telemedicine.is_(False))
    if max_fee is not None:
        doctors_query = doctors_query.filter(Doctor.consultation_fee <= max_fee)
    
    doctors = doctors_query.order_by(Doctor.consultation_fee.asc(), Doctor.experience_years.desc(), User.name.asc()).all()
    filtered_doctors = []
    for doctor in doctors:
        availability_calendar = get_doctor_availability_calendar(doctor.id)
        doctor.next_available_label = get_next_available_slot_label(availability_calendar)
        doctor.availability_calendar = availability_calendar
        doctor.has_open_slots = any(day['slots'] for day in availability_calendar)
        if availability_filter == 'available' and not doctor.has_open_slots:
            continue
        filtered_doctors.append(doctor)

    doctors = filtered_doctors
    specialties = db.session.query(Doctor.specialty).distinct()
    specialties = sorted([s[0] for s in specialties if s[0]])
    
    return render_template(
        'doctors_directory.html',
        doctors=doctors,
        specialties=specialties,
        selected_specialty=specialty,
        search_query=search_query,
        telemedicine_filter=telemedicine_filter,
        availability_filter=availability_filter,
        max_fee=max_fee
    )

@app.route('/doctor/<int:doctor_id>')
def doctor_profile(doctor_id):
    doctor = Doctor.query.get_or_404(doctor_id)
    completed_appointments = Appointment.query.filter(
        Appointment.doctor_id == doctor_id,
        Appointment.status == 'completed'
    ).count()
    availability_calendar = get_doctor_availability_calendar(doctor_id, days_ahead=7)
    next_available_label = get_next_available_slot_label(availability_calendar)
    return render_template(
        'doctor_profile.html',
        doctor=doctor,
        completed_appointments=completed_appointments,
        availability_calendar=availability_calendar,
        next_available_label=next_available_label
    )

@app.route('/book_appointment', methods=['GET', 'POST'])
@login_required
def book_appointment():
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))
    doctors = Doctor.query.all()
    selected_doctor_id = request.args.get('doctor_id', type=int)
    emergency_default = request.args.get('emergency') == '1'
    consultation_type_default = request.args.get('consultation_type', 'in-person')
    telemedicine_default = consultation_type_default == 'virtual'
    family_members = get_patient_family_members(current_user.id)
    
    # Generate date options for the next 7 days
    today = datetime.utcnow().date()
    date_options = []
    for i in range(7):
        date = today + timedelta(days=i)
        date_options.append({
            'date': date,
            'formatted': date.strftime('%Y-%m-%d'),
            'display': date.strftime('%a %d %b')
        })
    
    if request.method == 'POST':
        doctor_id = int(request.form['doctor_id'])
        family_member_id = request.form.get('family_member_id', type=int)
        appointment_date_str = request.form['appointment_date']
        appointment_time_str = request.form['appointment_time']
        phone_number = request.form.get('phone_number')
        is_emergency = request.form.get('is_emergency') == 'on'
        consultation_type = request.form.get('consultation_type', 'in-person')

        family_member = None
        if family_member_id:
            family_member = FamilyMember.query.filter_by(id=family_member_id, patient_id=current_user.id).first()
            if not family_member:
                flash('Selected family member was not found.')
                return redirect(url_for('book_appointment', doctor_id=doctor_id))
        
        doctor = Doctor.query.get(doctor_id)
        if consultation_type == 'virtual' and (not doctor or not doctor.allows_telemedicine):
            flash('Virtual consultations are only available for doctors who support telemedicine.')
            return redirect(url_for('book_appointment', doctor_id=doctor_id, consultation_type='virtual'))
        if doctor and not doctor.allows_telemedicine:
            consultation_type = 'in-person'
        
        appointment_date = datetime.strptime(appointment_date_str, '%Y-%m-%d').date()
        appointment_time = datetime.combine(appointment_date, parse_form_time(appointment_time_str))
        is_valid, message = validate_appointment_time(doctor_id, appointment_time)
        if not is_valid:
            flash(message)
            return redirect(url_for('book_appointment'))
        if phone_number:
            current_user.phone_number = phone_number
        appointment_code = generate_appointment_code()
        appointment = Appointment(
            patient_id=current_user.id,
            doctor_id=doctor_id,
            appointment_time=appointment_time,
            appointment_code=appointment_code,
            phone_number=phone_number,
            family_member_id=family_member.id if family_member else None,
            is_emergency=is_emergency,
            consultation_type=consultation_type,
            amount_due=doctor.consultation_fee if doctor else 0.0,
            payment_status='pending'
        )
        db.session.add(appointment)
        db.session.commit()
        
        # If emergency, also add to queue with priority
        if is_emergency:
            doctor = Doctor.query.get(doctor_id)
            last_queue = QueueRecord.query.filter_by(doctor_id=doctor_id).order_by(QueueRecord.queue_number.desc()).first()
            queue_number = (last_queue.queue_number + 1) if last_queue else 1
            emergency_queue = QueueRecord(
                patient_id=current_user.id,
                doctor_id=doctor_id,
                queue_number=queue_number,
                priority='emergency',
                estimated_wait=0
            )
            db.session.add(emergency_queue)
            db.session.commit()
            recalculate_wait_times(doctor_id)
        
        flash(f'Appointment booked successfully. Your code is {appointment_code}.')
        return redirect(url_for('dashboard'))
    return render_template(
        'book_appointment.html',
        doctors=doctors,
        date_options=date_options,
        selected_doctor_id=selected_doctor_id,
        emergency_default=emergency_default,
        consultation_type_default=consultation_type_default,
        telemedicine_default=telemedicine_default,
        family_members=family_members,
        insurance_profile=get_patient_insurance_profile(current_user.id)
    )

@app.route('/available_slots')
@login_required
def available_slots():
    doctor_id = request.args.get('doctor_id', type=int)
    date_str = request.args.get('date')
    exclude_appointment_id = request.args.get('exclude_appointment_id', type=int)
    if not doctor_id or not date_str:
        return jsonify({'slots': []})
    try:
        appointment_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'slots': []}), 400
    if appointment_date < datetime.utcnow().date():
        return jsonify({'slots': []})
    slots = get_available_times(doctor_id, appointment_date, exclude_appointment_id)
    return jsonify({
        'slots': [
            {'value': slot.strftime('%H:%M:%S'), 'label': slot.strftime('%I:%M %p')}
            for slot in slots
        ]
    })

@app.route('/cancel_appointment/<int:appointment_id>')
@login_required
def cancel_appointment(appointment_id):
    appointment = Appointment.query.get(appointment_id)
    if appointment and appointment.patient_id == current_user.id:
        appointment.status = 'cancelled'
        appointment.updated_at = datetime.utcnow()
        db.session.commit()
        flash('Appointment cancelled')
    return redirect(url_for('dashboard'))

@app.route('/reschedule_appointment/<int:appointment_id>', methods=['GET', 'POST'])
@login_required
def reschedule_appointment(appointment_id):
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))
    appointment = Appointment.query.get_or_404(appointment_id)
    if appointment.patient_id != current_user.id or appointment.status != 'scheduled':
        flash('Only scheduled appointments can be rescheduled.')
        return redirect(url_for('dashboard'))

    today = datetime.utcnow().date()
    date_options = []
    for i in range(14):
        date = today + timedelta(days=i)
        date_options.append({
            'date': date,
            'formatted': date.strftime('%Y-%m-%d'),
            'display': date.strftime('%a %d %b')
        })

    if request.method == 'POST':
        appointment_date = datetime.strptime(request.form['appointment_date'], '%Y-%m-%d').date()
        appointment_time = datetime.combine(appointment_date, parse_form_time(request.form['appointment_time']))
        is_valid, message = validate_appointment_time(appointment.doctor_id, appointment_time, appointment.id)
        if not is_valid:
            flash(message)
            return redirect(url_for('reschedule_appointment', appointment_id=appointment.id))
        appointment.appointment_time = appointment_time
        appointment.phone_number = request.form.get('phone_number') or appointment.phone_number
        appointment.updated_at = datetime.utcnow()
        db.session.commit()
        flash('Appointment rescheduled successfully.')
        return redirect(url_for('dashboard'))

    return render_template('reschedule_appointment.html', appointment=appointment, date_options=date_options)

@app.route('/leave_queue/<int:queue_id>')
@login_required
def leave_queue(queue_id):
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))
    queue_record = QueueRecord.query.get_or_404(queue_id)
    if queue_record.patient_id == current_user.id and queue_record.status == 'waiting':
        doctor_id = queue_record.doctor_id
        queue_record.status = 'cancelled'
        db.session.commit()
        recalculate_wait_times(doctor_id)
        flash('You have left the queue.')
    return redirect(url_for('dashboard'))

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        name = request.form['name'].strip()
        email = request.form['email'].strip().lower()
        phone_number = request.form.get('phone_number', '').strip() or None
        date_of_birth = request.form.get('date_of_birth', '').strip()
        current_user.date_of_birth = datetime.strptime(date_of_birth, '%Y-%m-%d').date() if date_of_birth else None
        current_user.gender = request.form.get('gender', '').strip() or None
        current_user.blood_group = request.form.get('blood_group', '').strip() or None
        current_user.allergies = request.form.get('allergies', '').strip() or None
        current_user.medical_conditions = request.form.get('medical_conditions', '').strip() or None
        current_user.emergency_contact = request.form.get('emergency_contact', '').strip() or None
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')

        existing_email = User.query.filter(User.email == email, User.id != current_user.id).first()
        if existing_email:
            flash('Email is already used by another account.')
            return redirect(url_for('profile'))
        if new_password and new_password != confirm_password:
            flash('Passwords do not match.')
            return redirect(url_for('profile'))

        current_user.name = name
        current_user.email = email
        current_user.phone_number = phone_number
        if new_password:
            current_user.set_password(new_password)
        db.session.commit()
        flash('Profile updated successfully.')
        return redirect(url_for('profile'))
    return render_template('profile.html')

@app.route('/join_queue', methods=['GET', 'POST'])
@login_required
def join_queue():
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))
    expire_stale_waiting_patients()
    doctors = Doctor.query.all()
    if request.method == 'POST':
        doctor_id = request.form['doctor_id']
        doctor = Doctor.query.get(doctor_id)
        if doctor:
            existing_queue = QueueRecord.query.filter(
                QueueRecord.patient_id == current_user.id,
                QueueRecord.doctor_id == doctor.id,
                QueueRecord.status.in_(['waiting', 'in_consultation'])
            ).first()
            if existing_queue:
                flash('You are already waiting or in consultation for this doctor.')
                return redirect(url_for('dashboard'))
            # Get next queue number
            last_queue = QueueRecord.query.filter_by(doctor_id=doctor_id).order_by(QueueRecord.queue_number.desc()).first()
            queue_number = (last_queue.queue_number + 1) if last_queue else 1
            # Calculate estimated wait
            waiting_count = QueueRecord.query.filter_by(doctor_id=doctor_id, status='waiting').count()
            estimated_wait = waiting_count * doctor.consultation_time
            queue_record = QueueRecord(patient_id=current_user.id, doctor_id=doctor_id, queue_number=queue_number, estimated_wait=estimated_wait)
            db.session.add(queue_record)
            db.session.commit()
            recalculate_wait_times(doctor_id)
            flash('Joined queue successfully')
            return redirect(url_for('dashboard'))
    return render_template('join_queue.html', doctors=doctors)

@app.route('/update_queue/<int:queue_id>/<action>')
@login_required
def update_queue(queue_id, action):
    if current_user.role != 'doctor':
        return redirect(url_for('dashboard'))
    expire_stale_waiting_patients()
    doctor = Doctor.query.filter_by(user_id=current_user.id).first()
    queue_record = QueueRecord.query.get(queue_id)
    if queue_record and queue_record.doctor_id == doctor.id:
        if action == 'start':
            if queue_record.status != 'waiting':
                flash('Only waiting patients can be started.')
                return redirect(url_for('dashboard'))
            active = QueueRecord.query.filter_by(doctor_id=doctor.id, status='in_consultation').first()
            if active and active.id != queue_record.id:
                flash('Please complete the current consultation first.')
                return redirect(url_for('dashboard'))
            queue_record.status = 'in_consultation'
        elif action == 'complete':
            if queue_record.status != 'in_consultation':
                flash('Only patients in consultation can be completed.')
                return redirect(url_for('dashboard'))
            queue_record.status = 'completed'
            appointment = Appointment.query.filter_by(
                doctor_id=doctor.id,
                patient_id=queue_record.patient_id,
                status='checked_in'
            ).order_by(Appointment.appointment_time.desc()).first()
            if appointment:
                appointment.status = 'completed'
                appointment.updated_at = datetime.utcnow()
            recalculate_wait_times(doctor.id)
        db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/admin_add_walkin', methods=['GET', 'POST'])
@login_required
def admin_add_walkin():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    doctors = Doctor.query.all()
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        name = request.form['name']
        phone_number = request.form.get('phone_number')
        doctor_id = request.form['doctor_id']
        priority = request.form.get('priority', 'normal')

        if User.query.filter_by(username=username).first() or User.query.filter_by(email=email).first():
            flash('Username or email already exists')
            return redirect(url_for('admin_add_walkin'))

        user = User(username=username, email=email, role='patient', name=name, phone_number=phone_number)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        doctor = Doctor.query.get(doctor_id)
        if doctor:
            last_queue = QueueRecord.query.filter_by(doctor_id=doctor_id).order_by(QueueRecord.queue_number.desc()).first()
            queue_number = (last_queue.queue_number + 1) if last_queue else 1
            waiting_count = QueueRecord.query.filter_by(doctor_id=doctor_id, status='waiting').count()
            estimated_wait = waiting_count * doctor.consultation_time
            queue_record = QueueRecord(patient_id=user.id, doctor_id=doctor_id, queue_number=queue_number, estimated_wait=estimated_wait, priority=priority)
            db.session.add(queue_record)
            db.session.commit()
            recalculate_wait_times(doctor_id)
            flash('Walk-in patient added and queued successfully')
            return redirect(url_for('dashboard'))

    return render_template('admin_add_walkin.html', doctors=doctors)

@app.route('/admin_add_doctor', methods=['GET', 'POST'])
@login_required
def admin_add_doctor():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        name = request.form['name']
        specialty = request.form['specialty']
        consultation_time = int(request.form.get('consultation_time', 15))
        photo_url = request.form.get('photo_url') or None
        bio = request.form.get('bio') or None
        contributions = request.form.get('contributions') or None
        allows_telemedicine = request.form.get('allows_telemedicine') == 'on'

        if User.query.filter_by(username=username).first():
            flash('Username already exists')
            return redirect(url_for('admin_add_doctor'))
        if User.query.filter_by(email=email).first():
            flash('Email already exists')
            return redirect(url_for('admin_add_doctor'))
        
        user = User(username=username, email=email, role='doctor', name=name)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        
        doctor = Doctor(
            user_id=user.id,
            specialty=specialty,
            consultation_time=consultation_time,
            photo_url=photo_url,
            bio=bio,
            contributions=contributions,
            allows_telemedicine=allows_telemedicine
        )
        db.session.add(doctor)
        db.session.commit()
        flash('Doctor added successfully')
        return redirect(url_for('dashboard'))
    return render_template('admin_add_doctor.html')

@app.route('/checkin_appointment', methods=['POST'])
@login_required
def checkin_appointment():
    if current_user.role != 'patient':
        return redirect(url_for('dashboard'))

    mark_missed_appointments()
    expire_stale_waiting_patients()
    appointment_code = request.form['appointment_code'].strip()
    appointment = Appointment.query.filter_by(
        appointment_code=appointment_code,
        patient_id=current_user.id,
        status='scheduled'
    ).first()

    if not appointment:
        flash('Invalid appointment code or appointment is not scheduled.')
        return redirect(url_for('qr_checkin'))

    checkin_message = appointment_checkin_message(appointment)
    if checkin_message:
        flash(checkin_message)
        return redirect(url_for('qr_checkin'))

    existing_queue = QueueRecord.query.filter(
        QueueRecord.patient_id == current_user.id,
        QueueRecord.doctor_id == appointment.doctor_id,
        QueueRecord.status.in_(['waiting', 'in_consultation'])
    ).first()

    if existing_queue:
        flash('You are already in the queue or in consultation for this doctor.')
        return redirect(url_for('dashboard'))

    doctor = Doctor.query.get(appointment.doctor_id)
    if not doctor:
        flash('Doctor not found for this appointment.')
        return redirect(url_for('qr_checkin'))

    last_queue = QueueRecord.query.filter_by(doctor_id=doctor.id).order_by(QueueRecord.queue_number.desc()).first()
    queue_number = (last_queue.queue_number + 1) if last_queue else 1
    waiting_count = QueueRecord.query.filter_by(doctor_id=doctor.id, status='waiting').count()
    estimated_wait = waiting_count * doctor.consultation_time

    queue_record = QueueRecord(
        patient_id=current_user.id,
        doctor_id=doctor.id,
        queue_number=queue_number,
        estimated_wait=estimated_wait
    )
    db.session.add(queue_record)
    appointment.status = 'checked_in'
    db.session.commit()
    recalculate_wait_times(doctor.id)

    flash(f'Check-in successful! You are queued for Dr. {doctor.user.name}.')
    return redirect(url_for('dashboard'))

@app.route('/analytics')
@login_required
def analytics():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    
    # Get today's stats
    today = datetime.utcnow().date()
    today_start = datetime.combine(today, datetime.min.time())
    today_end = datetime.combine(today, datetime.max.time())
    
    # Total patients today
    total_patients_today = QueueRecord.query.filter(
        QueueRecord.created_at >= today_start,
        QueueRecord.created_at <= today_end
    ).count()
    
    # Completed consultations today
    completed_today = QueueRecord.query.filter(
        QueueRecord.status == 'completed',
        QueueRecord.created_at >= today_start,
        QueueRecord.created_at <= today_end
    ).count()
    
    # Current waiting patients
    waiting_now = QueueRecord.query.filter_by(status='waiting').count()
    missed_today = Appointment.query.filter(
        Appointment.status == 'missed',
        Appointment.appointment_time >= today_start,
        Appointment.appointment_time <= today_end
    ).count()
    missed_week = Appointment.query.filter(
        Appointment.status == 'missed',
        Appointment.appointment_time >= today_start - timedelta(days=6),
        Appointment.appointment_time <= today_end
    ).count()
    
    # Average wait time today
    completed_records = QueueRecord.query.filter(
        QueueRecord.status == 'completed',
        QueueRecord.created_at >= today_start,
        QueueRecord.created_at <= today_end
    ).all()
    
    avg_wait_time = 0
    if completed_records:
        total_wait = sum((r.created_at - r.created_at).seconds // 60 for r in completed_records)  # Simplified
        avg_wait_time = total_wait // len(completed_records)
    
    # Doctor performance
    doctors = Doctor.query.all()
    doctor_stats = []
    for doctor in doctors:
        completed = QueueRecord.query.filter(
            QueueRecord.doctor_id == doctor.id,
            QueueRecord.status == 'completed',
            QueueRecord.created_at >= today_start,
            QueueRecord.created_at <= today_end
        ).count()
        missed = Appointment.query.filter(
            Appointment.doctor_id == doctor.id,
            Appointment.status == 'missed',
            Appointment.appointment_time >= today_start,
            Appointment.appointment_time <= today_end
        ).count()
        doctor_stats.append({
            'name': doctor.user.name,
            'specialty': doctor.specialty,
            'completed_today': completed,
            'missed_today': missed
        })
    
    return render_template('analytics.html', 
                         total_patients_today=total_patients_today,
                         completed_today=completed_today,
                         waiting_now=waiting_now,
                         missed_today=missed_today,
                         missed_week=missed_week,
                         avg_wait_time=avg_wait_time,
                         doctor_stats=doctor_stats)

@app.route('/appointment_details/<int:appointment_id>')
@login_required
def appointment_details(appointment_id):
    mark_missed_appointments()
    appointment = Appointment.query.get_or_404(appointment_id)
    
    # Check if user has permission to view this appointment
    if current_user.role == 'doctor':
        doctor = Doctor.query.filter_by(user_id=current_user.id).first()
        if not doctor or appointment.doctor_id != doctor.id:
            flash('Access denied')
            return redirect(url_for('dashboard'))
    elif current_user.role == 'patient':
        if appointment.patient_id != current_user.id:
            flash('Access denied')
            return redirect(url_for('dashboard'))
    else:
        flash('Access denied')
        return redirect(url_for('dashboard'))
    
    return render_template('appointment_details.html', appointment=appointment)

@app.route('/appointment_notes/<int:appointment_id>', methods=['POST'])
@login_required
def appointment_notes(appointment_id):
    if current_user.role != 'doctor':
        flash('Access denied')
        return redirect(url_for('dashboard'))

    appointment = Appointment.query.get_or_404(appointment_id)
    doctor = Doctor.query.filter_by(user_id=current_user.id).first()
    if not doctor or appointment.doctor_id != doctor.id:
        flash('Access denied')
        return redirect(url_for('dashboard'))

    appointment.notes = request.form.get('notes', '').strip() or None
    appointment.diagnosis = request.form.get('diagnosis', '').strip() or None
    appointment.prescription = request.form.get('prescription', '').strip() or None
    appointment.follow_up_advice = request.form.get('follow_up_advice', '').strip() or None
    appointment.updated_at = datetime.utcnow()
    db.session.commit()
    flash('Consultation notes saved.')
    return redirect(url_for('appointment_details', appointment_id=appointment.id))

@app.route('/schedule_followup/<int:appointment_id>', methods=['GET', 'POST'])
@login_required
def schedule_followup(appointment_id):
    if current_user.role != 'doctor':
        flash('Access denied')
        return redirect(url_for('dashboard'))
    
    original_appointment = Appointment.query.get_or_404(appointment_id)
    doctor = Doctor.query.filter_by(user_id=current_user.id).first()
    if not doctor or original_appointment.doctor_id != doctor.id:
        flash('Access denied')
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        followup_date_str = request.form['followup_date']
        followup_time_str = request.form['followup_time']
        
        followup_date = datetime.strptime(followup_date_str, '%Y-%m-%d').date()
        followup_time = datetime.combine(followup_date, parse_form_time(followup_time_str))
        
        is_valid, message = validate_appointment_time(doctor.id, followup_time)
        if not is_valid:
            flash(message)
            return redirect(url_for('schedule_followup', appointment_id=appointment_id))
        
        followup_code = generate_appointment_code()
        followup_appointment = Appointment(
            patient_id=original_appointment.patient_id,
            doctor_id=doctor.id,
            appointment_time=followup_time,
            appointment_code=followup_code,
            phone_number=original_appointment.phone_number,
            is_follow_up=True,
            follow_up_of=appointment_id,
            consultation_type='in-person'
        )
        db.session.add(followup_appointment)
        db.session.commit()
        flash(f'Follow-up appointment scheduled successfully. Code: {followup_code}')
        return redirect(url_for('appointment_details', appointment_id=appointment_id))
    
    # Generate date options for the next 30 days
    today = datetime.utcnow().date()
    date_options = []
    for i in range(1, 31):
        date = today + timedelta(days=i)
        date_options.append({
            'date': date,
            'formatted': date.strftime('%Y-%m-%d'),
            'display': date.strftime('%a %d %b')
        })
    
    return render_template('schedule_followup.html', original_appointment=original_appointment, date_options=date_options)

@app.route('/admin_queue/<int:queue_id>/<action>', methods=['POST'])
@login_required
def admin_queue_action(queue_id, action):
    if current_user.role != 'admin':
        flash('Access denied')
        return redirect(url_for('dashboard'))

    queue_record = QueueRecord.query.get_or_404(queue_id)
    doctor_id = queue_record.doctor_id

    if action == 'priority':
        queue_record.priority = request.form.get('priority', 'normal')
    elif action == 'remove' and queue_record.status in ['waiting', 'in_consultation']:
        queue_record.status = 'cancelled'
    elif action == 'up' and queue_record.status == 'waiting':
        previous_record = QueueRecord.query.filter(
            QueueRecord.doctor_id == doctor_id,
            QueueRecord.status == 'waiting',
            QueueRecord.queue_number < queue_record.queue_number
        ).order_by(QueueRecord.queue_number.desc()).first()
        if previous_record:
            previous_record.queue_number, queue_record.queue_number = queue_record.queue_number, previous_record.queue_number
    elif action == 'down' and queue_record.status == 'waiting':
        next_record = QueueRecord.query.filter(
            QueueRecord.doctor_id == doctor_id,
            QueueRecord.status == 'waiting',
            QueueRecord.queue_number > queue_record.queue_number
        ).order_by(QueueRecord.queue_number).first()
        if next_record:
            next_record.queue_number, queue_record.queue_number = queue_record.queue_number, next_record.queue_number
    else:
        flash('Invalid queue action.')
        return redirect(url_for('dashboard'))

    db.session.commit()
    recalculate_wait_times(doctor_id)
    flash('Queue updated.')
    return redirect(url_for('dashboard'))

@app.route('/doctor/patient_history/<int:patient_id>')
@login_required
def patient_history(patient_id):
    if current_user.role != 'doctor':
        flash('Access denied')
        return redirect(url_for('dashboard'))

    mark_missed_appointments()
    doctor = Doctor.query.filter_by(user_id=current_user.id).first()
    if not doctor:
        flash('Doctor profile not found')
        return redirect(url_for('dashboard'))

    patient = User.query.filter_by(id=patient_id, role='patient').first_or_404()
    appointments = Appointment.query.filter_by(
        doctor_id=doctor.id,
        patient_id=patient.id
    ).order_by(Appointment.appointment_time.desc()).all()
    queue_records = QueueRecord.query.filter_by(
        doctor_id=doctor.id,
        patient_id=patient.id
    ).order_by(QueueRecord.created_at.desc()).all()

    return render_template(
        'patient_history.html',
        patient=patient,
        doctor=doctor,
        appointments=appointments,
        queue_records=queue_records
    )

@app.route('/pay_appointment/<int:appointment_id>', methods=['POST'])
@login_required
def pay_appointment(appointment_id):
    appointment = Appointment.query.get_or_404(appointment_id)
    if current_user.role != 'patient' or appointment.patient_id != current_user.id:
        return {'success': False, 'message': 'Access denied'}, 403

    if appointment.payment_status == 'paid':
        return {'success': False, 'message': 'Appointment is already paid.'}, 400

    if appointment.status != 'scheduled':
        return {'success': False, 'message': 'Only scheduled appointments can be paid.'}, 400

    appointment.payment_status = 'paid'
    appointment.payment_method = 'credit_card'
    appointment.payment_reference = f'PAY-{uuid4().hex[:10].upper()}'
    appointment.paid_at = datetime.utcnow()
    appointment.updated_at = datetime.utcnow()
    db.session.commit()
    return {'success': True, 'message': 'Payment completed successfully.'}

@app.route('/update_appointment_status/<int:appointment_id>/<status>', methods=['POST'])
@login_required
def update_appointment_status(appointment_id, status):
    appointment = Appointment.query.get_or_404(appointment_id)
    
    # Check permissions
    if current_user.role == 'doctor':
        doctor = Doctor.query.filter_by(user_id=current_user.id).first()
        if not doctor or appointment.doctor_id != doctor.id:
            return {'success': False, 'message': 'Access denied'}, 403
    elif current_user.role == 'patient':
        if appointment.patient_id != current_user.id:
            return {'success': False, 'message': 'Access denied'}, 403
    else:
        return {'success': False, 'message': 'Access denied'}, 403
    
    # Update status
    if status in ['completed', 'cancelled']:
        appointment.status = status
        appointment.updated_at = datetime.utcnow()
        db.session.commit()
        return {'success': True, 'message': f'Appointment {status}'}
    
    return {'success': False, 'message': 'Invalid status'}, 400

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.')
    return redirect(url_for('home'))

@app.route('/qr_checkin')
def qr_checkin():
    return render_template('qr_checkin.html')

def upgrade_database():
    if db.engine.dialect.name == 'sqlite':
        result = db.session.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='queue_record'"))
        if result.first():
            columns = [row[1] for row in db.session.execute(text('PRAGMA table_info(queue_record)')).all()]
            if 'priority' not in columns:
                db.session.execute(text("ALTER TABLE queue_record ADD COLUMN priority VARCHAR(20) DEFAULT 'normal'"))
                db.session.commit()

        result = db.session.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='appointment'"))
        if result.first():
            columns = [row[1] for row in db.session.execute(text('PRAGMA table_info(appointment)')).all()]
            if 'appointment_code' not in columns:
                db.session.execute(text("ALTER TABLE appointment ADD COLUMN appointment_code VARCHAR(20) DEFAULT ''"))
                db.session.commit()
            if 'family_member_id' not in columns:
                db.session.execute(text("ALTER TABLE appointment ADD COLUMN family_member_id INTEGER DEFAULT NULL"))
                db.session.commit()
            if 'phone_number' not in columns:
                db.session.execute(text("ALTER TABLE appointment ADD COLUMN phone_number VARCHAR(50) DEFAULT NULL"))
                db.session.commit()
            if 'updated_at' not in columns:
                db.session.execute(text("ALTER TABLE appointment ADD COLUMN updated_at DATETIME DEFAULT NULL"))
                db.session.commit()
            for column_name in ('notes', 'diagnosis', 'prescription', 'follow_up_advice'):
                if column_name not in columns:
                    db.session.execute(text(f"ALTER TABLE appointment ADD COLUMN {column_name} TEXT DEFAULT NULL"))
                    db.session.commit()
            if 'amount_due' not in columns:
                db.session.execute(text("ALTER TABLE appointment ADD COLUMN amount_due FLOAT DEFAULT 0.0"))
                db.session.commit()
            if 'payment_status' not in columns:
                db.session.execute(text("ALTER TABLE appointment ADD COLUMN payment_status VARCHAR(20) DEFAULT 'pending'"))
                db.session.commit()
            if 'payment_method' not in columns:
                db.session.execute(text("ALTER TABLE appointment ADD COLUMN payment_method VARCHAR(50) DEFAULT NULL"))
                db.session.commit()
            if 'payment_reference' not in columns:
                db.session.execute(text("ALTER TABLE appointment ADD COLUMN payment_reference VARCHAR(100) DEFAULT NULL"))
                db.session.commit()
            if 'paid_at' not in columns:
                db.session.execute(text("ALTER TABLE appointment ADD COLUMN paid_at DATETIME DEFAULT NULL"))
                db.session.commit()
        result = db.session.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='user'"))
        if result.first():
            columns = [row[1] for row in db.session.execute(text('PRAGMA table_info(user)')).all()]
            if 'phone_number' not in columns:
                db.session.execute(text("ALTER TABLE user ADD COLUMN phone_number VARCHAR(50) DEFAULT NULL"))
                db.session.commit()
            if 'email_verified' not in columns:
                db.session.execute(text("ALTER TABLE user ADD COLUMN email_verified BOOLEAN DEFAULT 0"))
                db.session.commit()
            if 'email_verification_token' not in columns:
                db.session.execute(text("ALTER TABLE user ADD COLUMN email_verification_token VARCHAR(255) DEFAULT NULL"))
                db.session.commit()
            if 'email_verified_at' not in columns:
                db.session.execute(text("ALTER TABLE user ADD COLUMN email_verified_at DATETIME DEFAULT NULL"))
                db.session.commit()
            user_columns = {
                'date_of_birth': 'DATE',
                'gender': 'VARCHAR(50)',
                'blood_group': 'VARCHAR(10)',
                'allergies': 'TEXT',
                'medical_conditions': 'TEXT',
                'emergency_contact': 'VARCHAR(150)',
                'profile_picture': 'VARCHAR(250)'
            }
            for column_name, column_type in user_columns.items():
                if column_name not in columns:
                    db.session.execute(text(f"ALTER TABLE user ADD COLUMN {column_name} {column_type} DEFAULT NULL"))
                    db.session.commit()
        result = db.session.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='doctor'"))
        if result.first():
            columns = [row[1] for row in db.session.execute(text('PRAGMA table_info(doctor)')).all()]
            if 'photo_url' not in columns:
                db.session.execute(text("ALTER TABLE doctor ADD COLUMN photo_url VARCHAR(250) DEFAULT NULL"))
                db.session.commit()
            if 'bio' not in columns:
                db.session.execute(text("ALTER TABLE doctor ADD COLUMN bio TEXT DEFAULT NULL"))
                db.session.commit()
            if 'contributions' not in columns:
                db.session.execute(text("ALTER TABLE doctor ADD COLUMN contributions TEXT DEFAULT NULL"))
                db.session.commit()
            if 'consultation_fee' not in columns:
                db.session.execute(text("ALTER TABLE doctor ADD COLUMN consultation_fee FLOAT DEFAULT 500.0"))
                db.session.commit()

app.config['APP_INITIALIZED'] = True




@app.before_request
def ensure_application_data_initialized():
    if not app.config.get('APP_INITIALIZED'):
        initialize_database()
        app.config['APP_INITIALIZED'] = True


if __name__ == '__main__':
    debug_mode = True

    if socketio_available:
        socketio.run(
            app,
            host='0.0.0.0',
            debug=debug_mode,
            allow_unsafe_werkzeug=True
        )
    else:
        app.run(
            host='0.0.0.0',
            debug=debug_mode
        )