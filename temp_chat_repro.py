from app import app, db, User, Doctor, ChatConsultation, ChatMessage

app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')

with app.app_context():
    db.drop_all()
    db.create_all()
    patient = User(username='pat', email='pat@example.com', role='patient', name='Patient')
    patient.set_password('x')
    doctor_user = User(username='doc', email='doc@example.com', role='doctor', name='Doctor')
    doctor_user.set_password('x')
    db.session.add_all([patient, doctor_user])
    db.session.commit()
    doctor = Doctor(user_id=doctor_user.id, specialty='General', consultation_time=15)
    db.session.add(doctor)
    db.session.commit()
    consultation = ChatConsultation(patient_id=patient.id, doctor_id=doctor.id, topic='Test', status='active')
    db.session.add(consultation)
    db.session.commit()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['_user_id'] = str(doctor_user.id)
            sess['_fresh'] = True
        resp = client.post(f'/chat_consultation/{consultation.id}', data={'message': 'Hello from doctor'}, follow_redirects=True)
        print('status', resp.status_code)
        print('text snippet', resp.get_data(as_text=True)[:500])
        print('messages', ChatMessage.query.all())
