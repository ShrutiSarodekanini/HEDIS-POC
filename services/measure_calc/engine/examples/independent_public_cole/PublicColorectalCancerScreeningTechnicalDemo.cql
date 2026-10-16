library PublicColorectalCancerScreeningTechnicalDemo version '0.1.0'

using FHIR version '4.0.1'

include FHIRHelpers version '4.0.1'

parameter "Measurement Period" Interval<DateTime>
  default Interval[@2026-01-01T00:00:00.0, @2026-12-31T23:59:59.999]

context Patient

define "Has Qualifying Encounter During Measurement Period":
  exists (
    [Encounter] E
      where E.status in { 'finished', 'in-progress' }
        and E.period.start.value during day of "Measurement Period"
  )

define "Has Qualifying Colonoscopy":
  exists (
    [Procedure] P
      where P.status in { 'completed' }
        and exists (
          P.code.coding Coding
            where Coding.system.value = 'http://snomed.info/sct'
              and Coding.code.value = '73761001'
        )
        and (
          (P.performed is FHIR.dateTime
              and (P.performed as FHIR.dateTime).value during day of Interval[start of "Measurement Period" - 9 years, end of "Measurement Period"])
            or
          (P.performed is FHIR.Period
              and (
                ((P.performed as FHIR.Period).start is not null and (P.performed as FHIR.Period).start.value during day of Interval[start of "Measurement Period" - 9 years, end of "Measurement Period"])
                  or
                ((P.performed as FHIR.Period).end is not null and (P.performed as FHIR.Period).end.value during day of Interval[start of "Measurement Period" - 9 years, end of "Measurement Period"])
              ))
        )
  )

define "Has Qualifying FIT During Measurement Period":
  exists (
    [Procedure] P
      where P.status in { 'completed' }
        and exists (
          P.code.coding Coding
            where Coding.system.value = 'http://www.ama-assn.org/go/cpt'
              and Coding.code.value = '82274'
        )
        and (
          (P.performed is FHIR.dateTime
              and (P.performed as FHIR.dateTime).value during day of "Measurement Period")
            or
          (P.performed is FHIR.Period
              and (
                ((P.performed as FHIR.Period).start is not null and (P.performed as FHIR.Period).start.value during day of "Measurement Period")
                  or
                ((P.performed as FHIR.Period).end is not null and (P.performed as FHIR.Period).end.value during day of "Measurement Period")
              ))
        )
  )

define "Has Qualifying StoolDNA During Window":
  exists (
    [Procedure] P
      where P.status in { 'completed' }
        and exists (
          P.code.coding Coding
            where Coding.system.value = 'http://www.ama-assn.org/go/cpt'
              and Coding.code.value = '81528'
        )
        and (
          (P.performed is FHIR.dateTime
              and (P.performed as FHIR.dateTime).value during day of Interval[start of "Measurement Period" - 2 years, end of "Measurement Period"])
            or
          (P.performed is FHIR.Period
              and (
                ((P.performed as FHIR.Period).start is not null and (P.performed as FHIR.Period).start.value during day of Interval[start of "Measurement Period" - 2 years, end of "Measurement Period"])
                  or
                ((P.performed as FHIR.Period).end is not null and (P.performed as FHIR.Period).end.value during day of Interval[start of "Measurement Period" - 2 years, end of "Measurement Period"])
              ))
        )
  )

define "ipp":
  AgeInYearsAt(end of "Measurement Period") in Interval[46, 75]
    and "Has Qualifying Encounter During Measurement Period"

define "denom":
  "ipp"

define "numer":
  "Has Qualifying Colonoscopy"
    or "Has Qualifying FIT During Measurement Period"
    or "Has Qualifying StoolDNA During Window"
