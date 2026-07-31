import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import app, db, User, Doctor, ChatConsultation


def test_chat_page_uses_fallback_form_submission():
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')
    with app.app_context():
        db.drop_all()
        db.create_all()

        patient = User(username='pat2', email='pat2@example.com', role='patient', name='Patient Two')
        patient.set_password('x')
        doctor_user = User(username='doc2', email='doc2@example.com', role='doctor', name='Doctor Two')
        doctor_user.set_password('x')
        db.session.add_all([patient, doctor_user])
        db.session.commit()

        doctor = Doctor(user_id=doctor_user.id, specialty='General', consultation_time=15)
        db.session.add(doctor)
        db.session.commit()

        consultation = ChatConsultation(patient_id=patient.id, doctor_id=doctor.id, topic='Test chat', status='active')
        db.session.add(consultation)
        db.session.commit()

        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(doctor_user.id)
                sess['_fresh'] = True

            response = client.get(f'/chat_consultation/{consultation.id}')
            html = response.get_data(as_text=True)

            assert response.status_code == 200
            assert 'id="realtimeChatForm"' in html
            assert 'type="submit"' in html
            assert 'socket.io' not in html
