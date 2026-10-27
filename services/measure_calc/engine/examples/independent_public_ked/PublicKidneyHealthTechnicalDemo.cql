library PublicKidneyHealthTechnicalDemo version '0.1.0'

using FHIR version '4.0.1'

include FHIRHelpers version '4.0.1'

parameter "Measurement Period" Interval<DateTime>
  default Interval[@2026-01-01T00:00:00.0, @2026-12-31T23:59:59.999]

context Patient

define "Has Diabetes Condition":
  exists (
    [Condition] C
      where exists (
        C.code.coding Coding
          where Coding.system.value = 'http://snomed.info/sct'
            and Coding.code.value = '44054006'
      )
  )

define "Has Outpatient Encounter During Measurement Period":
  exists (
    [Encounter] E
      where E.status in { 'finished', 'in-progress' }
        and E.period.start.value during day of "Measurement Period"
  )

define "Has eGFR During Measurement Period":
  exists (
    [Observation] O
      where O.status in { 'final', 'amended', 'corrected' }
        and exists (
          O.code.coding Coding
            where Coding.system.value = 'http://loinc.org'
              and Coding.code.value in { '33914-3', '48642-3', '62238-1' }
        )
        and O.effective is FHIR.dateTime
        and (O.effective as FHIR.dateTime).value during day of "Measurement Period"
  )

define "Has uACR During Measurement Period":
  exists (
    [Observation] O
      where O.status in { 'final', 'amended', 'corrected' }
        and exists (
          O.code.coding Coding
            where Coding.system.value = 'http://loinc.org'
              and Coding.code.value in { '14959-1', '9318-7' }
        )
        and O.effective is FHIR.dateTime
        and (O.effective as FHIR.dateTime).value during day of "Measurement Period"
  )

define "ipp":
  AgeInYearsAt(end of "Measurement Period") in Interval[18, 85]
    and "Has Diabetes Condition"
    and "Has Outpatient Encounter During Measurement Period"

define "denom":
  "ipp"

define "numer":
  "Has eGFR During Measurement Period"
    and "Has uACR During Measurement Period"
