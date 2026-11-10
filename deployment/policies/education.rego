package education

import rego.v1

# Student-record access: the caller must hold a current grant for the student and both must
# belong to the same tenant. Grants are supplied in input from the source of truth on each
# request, so revocation takes effect immediately. Mirrors backend/app/policy.py.
default allow := false

allow if {
	input.grant == true
	input.user.tenant_id == input.student.tenant_id
}
