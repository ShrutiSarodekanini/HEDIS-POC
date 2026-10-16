library PublicBreastCancerScreeningTechnicalDemo version '0.1.0'

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

define "Has Mammography During Allowed Window":
  exists (
    [Procedure] P
      where P.status in { 'completed' }
        and exists (
          P.code.coding Coding
            where (Coding.system.value = 'http://www.ama-assn.org/go/cpt' and Coding.code.value in { '77063', '77056' })
              or (Coding.system.value = 'http://snomed.info/sct' and Coding.code.value = '168749009')
        )
        and P.performed is FHIR.dateTime
        and (P.performed as FHIR.dateTime).value during day of Interval[DateTime(2024, 10, 1, 0, 0, 0, 0, 0), end of "Measurement Period"]
  )

define "ipp":
  AgeInYearsAt(end of "Measurement Period") in Interval[52, 74]
    and Patient.gender.value = 'female'
    and "Has Qualifying Encounter During Measurement Period"

define "denom":
  "ipp"

define "numer":
  "Has Mammography During Allowed Window"
