from datetime import date
from pathlib import Path
import textwrap


OUTPUT = Path("Hospital_Queue_Proposal.pdf")
PAGE_WIDTH = 612
PAGE_HEIGHT = 792
MARGIN_X = 58
TOP_Y = 720
BOTTOM_Y = 72
LINE_HEIGHT = 14


def pdf_escape(text):
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class SimplePDF:
    def __init__(self):
        self.pages = []
        self.current = []
        self.y = TOP_Y

    def new_page(self):
        if self.current:
            self.pages.append(self.current)
        self.current = []
        self.y = TOP_Y

    def ensure_space(self, lines=1):
        if self.y - (lines * LINE_HEIGHT) < BOTTOM_Y:
            self.new_page()

    def text(self, value, x=MARGIN_X, size=11, font="F1", leading=LINE_HEIGHT):
        self.ensure_space()
        self.current.append((x, self.y, size, font, value))
        self.y -= leading

    def spacer(self, height=10):
        self.y -= height

    def heading(self, value):
        self.ensure_space(3)
        self.spacer(8)
        self.text(value, size=15, font="F2", leading=18)
        self.spacer(2)

    def paragraph(self, value, width=88):
        for line in textwrap.wrap(value, width=width):
            self.text(line)
        self.spacer(6)

    def bullet(self, value, width=84):
        lines = textwrap.wrap(value, width=width)
        if not lines:
            return
        self.text("- " + lines[0])
        for line in lines[1:]:
            self.text("  " + line)
        self.spacer(2)

    def numbered(self, number, value, width=82):
        lines = textwrap.wrap(value, width=width)
        self.text(f"{number}. {lines[0]}")
        for line in lines[1:]:
            self.text("   " + line)
        self.spacer(2)

    def save(self, path):
        if self.current:
            self.pages.append(self.current)

        objects = []

        def add_object(body):
            objects.append(body)
            return len(objects)

        font_helvetica = add_object("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
        font_bold = add_object("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")

        page_refs = []
        content_refs = []
        for page in self.pages:
            stream_lines = ["BT"]
            for x, y, size, font, value in page:
                stream_lines.append(f"/{font} {size} Tf")
                stream_lines.append(f"{x} {y} Td")
                stream_lines.append(f"({pdf_escape(value)}) Tj")
                stream_lines.append(f"{-x} {-y} Td")
            stream_lines.append("ET")
            stream = "\n".join(stream_lines)
            content_refs.append(add_object(f"<< /Length {len(stream.encode('latin-1'))} >>\nstream\n{stream}\nendstream"))
            page_refs.append(None)

        kids = []
        pages_ref = len(objects) + len(self.pages) + 1
        for i, content_ref in enumerate(content_refs):
            page_ref = add_object(
                f"<< /Type /Page /Parent {pages_ref} 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
                f"/Resources << /Font << /F1 {font_helvetica} 0 R /F2 {font_bold} 0 R >> >> "
                f"/Contents {content_ref} 0 R >>"
            )
            page_refs[i] = page_ref
            kids.append(f"{page_ref} 0 R")

        pages_obj = add_object(f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(self.pages)} >>")
        catalog_obj = add_object(f"<< /Type /Catalog /Pages {pages_obj} 0 R >>")

        output = ["%PDF-1.4\n%\xE2\xE3\xCF\xD3\n"]
        offsets = [0]
        byte_count = len(output[0].encode("latin-1"))
        for i, body in enumerate(objects, 1):
            offsets.append(byte_count)
            obj = f"{i} 0 obj\n{body}\nendobj\n"
            output.append(obj)
            byte_count += len(obj.encode("latin-1"))

        xref_offset = byte_count
        xref = [f"xref\n0 {len(objects) + 1}\n", "0000000000 65535 f \n"]
        for offset in offsets[1:]:
            xref.append(f"{offset:010d} 00000 n \n")
        trailer = (
            f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_obj} 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF"
        )
        output.extend(xref)
        output.append(trailer)
        path.write_bytes("".join(output).encode("latin-1"))


def build_pdf():
    pdf = SimplePDF()
    today = date.today().strftime("%B %d, %Y")

    pdf.text("Project Proposal", size=22, font="F2", leading=28)
    pdf.text("Hospital Queue and Appointment Management System", size=18, font="F2", leading=24)
    pdf.spacer(12)
    pdf.text(f"Prepared on: {today}", size=11)
    pdf.text("Prepared for: Academic / Mini Project Submission", size=11)
    pdf.spacer(18)
    pdf.paragraph(
        "This proposal presents a proposed hospital web platform that will begin with queue and appointment management "
        "and later expand into a broader digital patient services system. The aim is to create a structured, modern, "
        "and scalable website that improves access to care, reduces manual coordination, and supports both patients "
        "and hospital staff through a single online platform."
    )

    pdf.heading("1. Project Topic / Title")
    pdf.paragraph("Hospital Queue and Appointment Management System with Extended Patient Services")

    pdf.heading("2. Introduction")
    pdf.paragraph(
        "Hospitals increasingly need digital systems that can organize appointments, simplify patient support, and provide "
        "clear access to services without overloading front-desk staff. In many facilities, patients still rely on phone "
        "calls, paper records, and physical queues to request care. This proposal outlines a website that will digitize the "
        "core hospital booking process and later support a wider range of patient-facing services such as consultations, "
        "medical tests, reminders, payments, and support tools."
    )

    pdf.heading("3. Aim of the Project")
    pdf.paragraph(
        "The main aim of this project is to design and develop a hospital website that makes it easier for patients to "
        "book services, communicate with hospital staff, and manage their healthcare needs online while helping the hospital "
        "organize appointments, records, and service requests in a more efficient and transparent way."
    )

    pdf.heading("4. Specific Objectives of the Project")
    for item in [
        "Provide an online platform for doctor appointment booking, follow-up scheduling, and appointment cancellation or rescheduling.",
        "Support specialist consultations, nurse consultation booking, and future telemedicine or virtual consultation requests.",
        "Allow patients to search doctors by specialty and review doctor profiles with photos, qualifications, and experience.",
        "Offer medical service booking for laboratory tests, blood tests, imaging, vaccinations, check-up packages, and health assessments.",
        "Enable patient support features such as registration, medical history access, reminders, notifications, ambulance requests, and hospital directions.",
        "Prepare the website for payment services, insurance support, billing history, receipts, and estimated treatment costs.",
        "Create a foundation for future modern features such as symptom checking, live chat, patient ratings, health articles, chatbot support, and family account management.",
    ]:
        pdf.bullet(item)

    pdf.heading("5. Problem Statement")
    pdf.paragraph(
        "Many hospital services are still fragmented across phone calls, paper forms, and in-person visits. This often leads to "
        "long waiting times, missed appointments, poor visibility of doctor availability, weak communication with patients, and "
        "difficulty accessing support services quickly. Patients may struggle to find the right doctor, understand available times, "
        "or keep track of reminders and records. A unified online hospital system is needed to make service access simpler, faster, "
        "and more reliable."
    )

    pdf.heading("6. Project Scope")
    for item in [
        "Core booking services: doctor appointment booking, specialist consultations, online/virtual consultations, emergency appointment requests, follow-up appointments, and booking changes.",
        "Doctor and staff services: doctor profiles, availability calendar, doctor search by specialty, location, availability or fees, and nurse consultation booking.",
        "Medical services: laboratory test booking, medical check-up packages, health screening, blood tests, X-ray and imaging bookings, vaccination appointments, and assessment packages.",
        "Patient support services: ambulance request, hospital map/directions, insurance information, registration, medical history records, patient dashboard, reminders, and notifications.",
        "Payment services: online payment, insurance claim support, billing history, receipts, and estimated treatment costs.",
        "Extra modern features: AI symptom checker, live chat, patient reviews and ratings, health blog, language selection, 24/7 chatbot support, and family account management.",
    ]:
        pdf.bullet(item)

    pdf.heading("7. Motivation of the Project")
    pdf.paragraph(
        "The motivation behind this project is the growing need for a hospital platform that feels modern, convenient, and easy "
        "to use. Patients expect faster digital access to healthcare information and services, while hospitals need tools that reduce "
        "manual workload and improve organization. By starting with a booking and queue management system and expanding it into a "
        "multi-service portal, the project can address real service gaps and provide a practical base for future digital growth."
    )

    pdf.heading("8. Justification of the Project")
    pdf.paragraph(
        "This project is justified because it directly addresses common problems in hospital service delivery, including long queues, "
        "scattered records, limited appointment visibility, and weak communication between patients and service providers. A digital "
        "system improves efficiency, supports better patient experience, and creates a foundation for future automation. The proposed "
        "features are also realistic for gradual implementation, allowing the website to grow in phases rather than being built all at once."
    )

    pdf.heading("9. Project Methodology")
    pdf.heading("9.1 Project Approach")
    for item in [
        "Phase 1: build the core booking, queue, and account management system.",
        "Phase 2: extend the platform with doctor profiles, specialist search, medical services, and support features.",
        "Phase 3: add payment tools, notifications, chatbot support, reviews, language options, and other advanced services.",
        "Phase 4: test the complete system, refine usability, and prepare for deployment and documentation.",
    ]:
        pdf.bullet(item)

    pdf.heading("9.2 Development Tools")
    for item in [
        "Frontend: HTML templates, CSS, Bootstrap, and JavaScript for responsive user interfaces.",
        "Backend: Python Flask for routing, validation, and application logic.",
        "Database: SQLite with SQLAlchemy for structured data storage and relationships.",
        "Authentication: Flask-Login and Werkzeug password hashing.",
        "Document production: Python-based PDF generation for proposal and project documentation.",
    ]:
        pdf.bullet(item)

    pdf.heading("10. Project Deliverables")
    for item in [
        "A well-structured proposal document in PDF form.",
        "A working hospital website prototype with appointment and queue management.",
        "Responsive web pages for patient, doctor, and administrator use.",
        "Database models and backend logic for user accounts, doctors, bookings, and service requests.",
        "A phased roadmap for adding the remaining advanced features after the initial version is completed.",
    ]:
        pdf.bullet(item)

    pdf.heading("11. Conclusion")
    pdf.paragraph(
        "In conclusion, this project proposes a modern hospital web platform that begins with practical appointment and queue "
        "management and expands toward a broader digital healthcare service portal. The proposal provides a clear direction for "
        "building the system in stages, starting with essential functions and later adding the wider set of patient support, medical, "
        "and payment features that will make the website more complete and useful."
    )

    pdf.save(OUTPUT)


if __name__ == "__main__":
    build_pdf()
    print(f"Created {OUTPUT.resolve()}")
