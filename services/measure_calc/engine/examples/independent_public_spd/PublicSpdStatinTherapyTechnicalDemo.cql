library PublicSpdStatinTherapyTechnicalDemo version '0.1.0'

using FHIR version '4.0.1'

include FHIRHelpers version '4.0.1'

valueset "Diabetes Diagnoses": 'http://example.org/fhir/ValueSet/public-spd-diabetes-diagnoses'
valueset "Statin Therapies": 'http://example.org/fhir/ValueSet/public-spd-statin-therapies'

parameter "Measurement Period" Interval<DateTime>
  default Interval[@2026-01-01T00:00:00.0, @2026-12-31T23:59:59.999]

context Patient

define "Has Diabetes Condition":
  exists (
    [Condition: "Diabetes Diagnoses"] C
      where C.onset is FHIR.dateTime
        and (C.onset as FHIR.dateTime).value on or before end of "Measurement Period"
  )

define "Has Qualifying Encounter During Measurement Period":
  exists (
    [Encounter] E
      where E.status in { 'finished', 'in-progress' }
        and E.period.start.value during day of "Measurement Period"
  )

define "In-Period Qualifying Statin Orders":
  [MedicationRequest: "Statin Therapies"] M
    where M.status in { 'active', 'completed' }
      and M.intent.value = 'order'
      and M.authoredOn.value during day of "Measurement Period"

define "ipp":
  AgeInYearsAt(end of "Measurement Period") in Interval[40, 75]
    and "Has Diabetes Condition"
    and "Has Qualifying Encounter During Measurement Period"

define "denom":
  "ipp"

define "numer":
  exists("In-Period Qualifying Statin Orders")
