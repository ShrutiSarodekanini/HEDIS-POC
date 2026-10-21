library PublicEedDiabetesEyeExamTechnicalDemo version '0.1.0'

using FHIR version '4.0.1'

include FHIRHelpers version '4.0.1'

valueset "Diabetes Diagnoses": 'http://example.org/fhir/ValueSet/public-eed-diabetes-diagnoses'
valueset "Diabetic Retinopathy Diagnoses": 'http://example.org/fhir/ValueSet/public-eed-diabetic-retinopathy-diagnoses'
valueset "Retinal Exam Findings": 'http://example.org/fhir/ValueSet/public-eed-retinal-exam-findings'

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

define "Has Diabetic Retinopathy":
  exists (
    [Condition: "Diabetic Retinopathy Diagnoses"] C
      where C.onset is FHIR.dateTime
        and (C.onset as FHIR.dateTime).value on or before end of "Measurement Period"
  )

define "Retinal Exams During Measurement Period":
  [Observation: "Retinal Exam Findings"] O
    where O.status in { 'final', 'amended', 'corrected' }
      and O.effective is FHIR.dateTime
      and (O.effective as FHIR.dateTime).value during day of "Measurement Period"

define "Retinal Exams During OneYearLookbackOrMeasurementPeriod":
  [Observation: "Retinal Exam Findings"] O
    where O.status in { 'final', 'amended', 'corrected' }
      and O.effective is FHIR.dateTime
      and (O.effective as FHIR.dateTime).value during day of Interval[(start of "Measurement Period" - 1 year), end of "Measurement Period"]

define "ipp":
  AgeInYearsAt(end of "Measurement Period") in Interval[18, 75]
    and "Has Diabetes Condition"
    and "Has Qualifying Encounter During Measurement Period"

define "denom":
  "ipp"

define "numer":
  if "Has Diabetic Retinopathy"
    then exists("Retinal Exams During Measurement Period")
    else exists("Retinal Exams During OneYearLookbackOrMeasurementPeriod")
