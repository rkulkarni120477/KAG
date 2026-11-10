package education

import rego.v1

# Student-record access requires all of:
#   - a current grant from the source of truth (read on every request, so revocation is immediate),
#   - the same tenant for caller and student,
#   - a purpose the caller's role is permitted to use.
# Teachers: instruction. Advisors: advising. Students: self_service on their own record only.
# Administrators have no student-record purpose and are denied. Mirrors backend/app/policy.py.
default allow := false

allow if {
	input.grant == true
	input.user.tenant_id == input.student.tenant_id
	role_may_use_purpose
}

role_may_use_purpose if {
	input.user.role == "teacher"
	input.purpose == "instruction"
}

role_may_use_purpose if {
	input.user.role == "advisor"
	input.purpose == "advising"
}

role_may_use_purpose if {
	input.user.role == "student"
	input.purpose == "self_service"
	input.student.user_id == input.user.id
}
