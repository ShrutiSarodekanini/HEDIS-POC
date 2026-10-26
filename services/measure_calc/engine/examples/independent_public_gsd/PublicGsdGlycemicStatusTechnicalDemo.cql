library PublicGsdGlycemicStatusTechnicalDemo version '0.1.0'

using FHIR version '4.0.1'

include FHIRHelpers version '4.0.1'

valueset "Diabetes Diagnoses": 'http://example.org/fhir/ValueSet/public-gsd-diabetes-diagnoses'
valueset "Glycemic Status Assessments": 'http://example.org/fhir/ValueSet/public-gsd-glycemic-status-assessments'

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

define "In-Period Glycemic Assessments":
  [Observation: "Glycemic Status Assessments"] O
    where O.status in { 'final', 'amended', 'corrected' }
      and O.effective is FHIR.dateTime
      and (O.effective as FHIR.dateTime).value during day of "Measurement Period"
      and O.value is FHIR.Quantity
      and (O.value as FHIR.Quantity).unit.value = '%'

define "Most Recent Glycemic Status Date":
  Max(
    ("In-Period Glycemic Assessments" O
      return date from (O.effective as FHIR.dateTime).value
    )
  )

define "Lowest Glycemic Value On Most Recent Date":
  Min(
    ("In-Period Glycemic Assessments" O
      where date from (O.effective as FHIR.dateTime).value = "Most Recent Glycemic Status Date"
      return (O.value as FHIR.Quantity).value
    )
  )

define "Has Most Recent Glycemic Assessment Without Result":
  exists (
    [Observation: "Glycemic Status Assessments"] O
      where O.status in { 'final', 'amended', 'corrected' }
        and O.effective is FHIR.dateTime
        and date from (O.effective as FHIR.dateTime).value = "Most Recent Glycemic Status Date"
        and O.value is null
  )

define "Has No In-Period Glycemic Assessment Record":
  not exists("In-Period Glycemic Assessments")

define "Has Elevated Most Recent Glycemic Reading":
  "Lowest Glycemic Value On Most Recent Date" > 9.0

define "ipp":
  AgeInYearsAt(end of "Measurement Period") in Interval[18, 75]
    and "Has Diabetes Condition"
    and "Has Qualifying Encounter During Measurement Period"

define "denom":
  "ipp"

define "numer":
  "Has Elevated Most Recent Glycemic Reading"
    or "Has Most Recent Glycemic Assessment Without Result"
    or "Has No In-Period Glycemic Assessment Record"
