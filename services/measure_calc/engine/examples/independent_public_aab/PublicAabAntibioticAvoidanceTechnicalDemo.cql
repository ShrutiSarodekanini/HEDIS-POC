library PublicAabAntibioticAvoidanceTechnicalDemo version '0.1.0'

using FHIR version '4.0.1'

include FHIRHelpers version '4.0.1'

valueset "Acute Bronchitis Diagnoses": 'http://example.org/fhir/ValueSet/public-aab-acute-bronchitis'
valueset "Antibiotic Medications": 'http://example.org/fhir/ValueSet/public-aab-antibiotic-medications'

parameter "Measurement Period" Interval<DateTime>
  default Interval[@2019-01-01T00:00:00.0, @2019-12-31T23:59:59.999]

context Patient

define "Bronchitis Conditions During Measurement Period":
  [Condition: "Acute Bronchitis Diagnoses"] C
    where C.onset is FHIR.dateTime
      and (C.onset as FHIR.dateTime).value during day of "Measurement Period"

define "Qualifying Encounters During Measurement Period":
  [Encounter] E
    where E.status = 'finished'
      and E.period.start.value during day of "Measurement Period"
      and exists (
        "Bronchitis Conditions During Measurement Period" C
          where C.encounter.reference.value = 'urn:uuid:' + E.id.value
             or C.encounter.reference.value = 'Encounter/' + E.id.value
      )

define "Antibiotic Ordered Near Qualifying Encounter":
  exists (
    "Qualifying Encounters During Measurement Period" E
      where exists (
        [MedicationRequest: "Antibiotic Medications"] R
          where R.status in { 'active', 'completed' }
            and R.authoredOn is FHIR.dateTime
            and (R.authoredOn as FHIR.dateTime).value during Interval[(E.period.start.value - 3 days), (E.period.start.value + 3 days)]
      )
  )

define "ipp":
  AgeInYearsAt(end of "Measurement Period") >= 3
    and exists("Qualifying Encounters During Measurement Period")

define "denom":
  "ipp"

define "numer":
  "denom"
    and not "Antibiotic Ordered Near Qualifying Encounter"
