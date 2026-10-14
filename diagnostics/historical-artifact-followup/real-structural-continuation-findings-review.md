# Descriptive continuation: findings review

Run: `run_18d9a9bb215b4a7d82d66aa2107983a6`.
Decision: `decision_a14d56c1e05e4fd4980e499e88ec61b8` (CR-102).
Context SHA256: `d3b5f6fc49f0b9e0d2b98658a48be303502ecd0033f6714cbc1839826c01fa70`.

## Executed scope and deterministic evidence

The approved Data task executed local `profile_dataset(include_plots=true)` and published the
profile JSON, distributions PNG and correlation PNG. Completion verifies all three promised
outputs with no missing artifact or validation failure. The exact approved plan resumed
without replanning. Audit 582 is `passed_with_warnings`: 14 controls pass, 16 do not apply,
and zero fail. A18 passes. The event chain and artifact manifest verify.

The review context establishes bounded local descriptive profiling. This approval concerns
closing that descriptive continuation with its recorded warnings. The executed scope contains
no training or operational credit decision.

## Model proposals requiring human review

There are 24 proposals: 16 HIGH and 8 MEDIUM, with zero CRITICAL. Each has `supported=null`;
verification remains uncertain. The model proposes concerns about the following controls:

| Control | Proposals | Severity |
| --- | ---: | --- |
| model:CREDIT-PROTECTED-ATTRIBUTE | 4 | HIGH |
| model:CREDIT-ADVERSE-ACTION | 4 | HIGH |
| model:CREDIT-SUBGROUP-DISPARITY | 4 | MEDIUM |
| model:EU-AI-ACT-ART-12 | 4 | HIGH |
| model:EU-AI-ACT-ART-14 | 4 | MEDIUM |
| model:EU-AI-ACT-ART-11 | 4 | HIGH |

## Individual proposals

### 1. Handling of Sensitive Attributes

- Finding: `finding_c34f3517888542b6b87c2152581e841a`
- Control: `model:CREDIT-PROTECTED-ATTRIBUTE`
- Severity: HIGH
- Verification: `supported=null`
- Applicability: applicable

Model observation:

The dataset contains sensitive attributes such as age and personal status (sex), which are used in the analysis. There is a risk that these sensitive attributes may affect the fairness of the lending decisions. "Activities or data inputs involving special categories of personal data are prohibited" (Directive (EU) 2023/2225, Article 18(3)-(4)).

Verification rationale:

The finding indicates that sensitive attributes such as age and personal status (sex) are used in the analysis, posing risks to the fairness of lending decisions. However, the projection does not provide sufficient evidence of the dataset's specific composition, nor does it clarify whether sensitive attributes were justified for inclusion in the analysis. Since the visibility of recorded evidence regarding sensitive attribute usage is insufficient to determine its truth, supported is set to null.

Model recommendation:

Review the dataset to ensure that sensitive attributes are either justified for inclusion or excluded from model features.

### 2. Automated Assessment Explainability Rights

- Finding: `finding_b6a874f03da94fabbe9a28206f78802b`
- Control: `model:CREDIT-ADVERSE-ACTION`
- Severity: HIGH
- Verification: `supported=null`
- Applicability: applicable

Model observation:

The evidence does not clearly indicate whether consumers can obtain a comprehensible explanation of the creditworthiness assessment, express a viewpoint, or request a human review, as required by Directive (EU) 2023/2225, Article 18(8)-(9).

Verification rationale:

The candidate finding addresses whether consumers can obtain a comprehensible explanation of the creditworthiness assessment and related rights, which may require specific recorded evidence about consumer interaction and feedback opportunities. However, the visible evidence does not include pertinent records to assess these claims directly. As a result, it remains unclear if the requirements for consumer explanation and review access are met or not, leading to an inability to support or refute the finding.

Model recommendation:

Establish procedures ensuring that consumers are informed about their rights and have access to explanations of their creditworthiness assessments.

### 3. Potential Subgroup Disparity

- Finding: `finding_a6131f8a4a094a9fa26ee09b114ab390`
- Control: `model:CREDIT-SUBGROUP-DISPARITY`
- Severity: MEDIUM
- Verification: `supported=null`
- Applicability: undetermined

Model observation:

There is no evidence presented to assess whether performance or selection rates diverge across groups defined by sensitive attributes. Consequently, subgroup disparity cannot be ruled out based on the provided information.

Verification rationale:

The candidate finding claims that there's no evidence to assess performance or selection rates across groups defined by sensitive attributes, thus subgroup disparity cannot be ruled out. However, the visible evidence doesn't include the necessary content to conclusively support or refute this claim, as it only references events without exposing their detailed contents. The audit mode is IN_FLIGHT and evidence content is required for a clear determination.

Model recommendation:

Implement systematic monitoring to evaluate performance and selection rates across different demographic groups to ensure no significant disparities exist.

### 4. Insufficient record-keeping regarding activity's purpose

- Finding: `finding_c77e6c1eb8924b35ba51ed49947cb050`
- Control: `model:EU-AI-ACT-ART-12`
- Severity: HIGH
- Verification: `supported=null`
- Applicability: applicable

Model observation:

The events logged in the audit report do not provide a comprehensive traceable record that aligns with the purpose of the activity as required by Article 12. Although there are recorded events indicating actions taken, the overall integrity and completeness of the log is uncertain. Event sequence 28 indicates the recording of the activity profile, but further details regarding the completeness of actions and decisions are needed.

Verification rationale:

The evidence does not provide sufficient information regarding the completeness and traceability of the logged events corresponding to the activity as required by Article 12. While event sequence 28 indicates the recording of the activity profile, material content needed to determine overall integrity and completeness of actions and decisions is absent.

Model recommendation:

Enhance the logging mechanism to ensure all relevant actions and decisions are documented in a traceable manner aligned with the activity's purpose.

### 5. Unclear human oversight capabilities

- Finding: `finding_28337ce9e33c4463a6472ab3e6307234`
- Control: `model:EU-AI-ACT-ART-14`
- Severity: MEDIUM
- Verification: `supported=null`
- Applicability: applicable

Model observation:

While the activity profile states that a Lead Data Scientist and Credit Risk Manager may intervene, the evidence does not clearly show the ability of these individuals to effectively override, intervene in, or stop the automated processes at critical decision points. The oversight structure requires further clarity to demonstrate compliance with Article 14.

Verification rationale:

The candidate finding concerns the clarity of the human oversight structure in relation to automated processes during decision-making. The available evidence does not contain specific details about the mechanisms for human intervention, which is crucial to validate the claim made in the finding. While the activity profile notes the presence of a Lead Data Scientist and Credit Risk Manager, it lacks material evidence showing that these roles can effectively intervene at critical decision points. Therefore, without the content of the omitted records, it remains unclear if such oversight exists. Thus, I assess the evidence regarding oversight capabilities as insufficiently detailed to reach a conclusion, leading to a determination of null support for the finding.

Model recommendation:

Clearly document the specific mechanisms and processes that allow for human intervention.

### 6. Insufficient evidence of technical documentation

- Finding: `finding_764aeb1e453a4fe3b421638ecb6a437e`
- Control: `model:EU-AI-ACT-ART-11`
- Severity: HIGH
- Verification: `supported=null`
- Applicability: applicable

Model observation:

The output does not contain sufficient technical documentation that meets the requirements outlined in Article 11 of the EU AI Act. There is no mention of the design, capabilities, limitations, or monitoring of the system being documented and kept up to date. This is necessary for compliance and assessment.

Verification rationale:

The evidence indicates that while there are activities related to analysis and oversight, the specific technical documentation required by Article 11 of the EU AI Act is not directly addressed. The projection shows no references to design, capabilities, limitations, or monitoring of the system, which are critical aspects of compliance. However, due to the absence of the detailed content of the technical documentation itself in the evidence, I cannot definitively conclude if the output meets or fails these requirements. Therefore, supported remains undetermined given the need for documentation details that are not present.

Model recommendation:

Develop and maintain comprehensive technical documentation as per the requirements specified in Article 11.

### 7. Use of Sensitive Attributes in Dataset

- Finding: `finding_1d70840fb4c843f09e43f68180802c9b`
- Control: `model:CREDIT-PROTECTED-ATTRIBUTE`
- Severity: HIGH
- Verification: `supported=null`
- Applicability: applicable

Model observation:

The dataset contains sensitive attributes such as age and personal status (sex), which are used in the analysis. There ...

Verification rationale:

The dataset contains sensitive attributes such as age and personal status (sex), which are mentioned in the candidate finding, but the actual dataset contents and examples of how these attributes are utilized in the analysis are not visible in the evidence. Therefore, it's unclear if they are indeed being used in the manner the finding suggests.

Model recommendation:

Ensure that sensitive attributes are not included as model features or assess the potential impact of their use.

### 8. Lack of Clear Explanation for Automated Processing

- Finding: `finding_9ee86a7275324fc69bf74fda59be535e`
- Control: `model:CREDIT-ADVERSE-ACTION`
- Severity: HIGH
- Verification: `supported=null`
- Applicability: applicable

Model observation:

The evidence does not clearly indicate whether consumers can obtain a comprehensible explanation of the creditworthiness assessment including its logic, risks, significance and effects.

Verification rationale:

The evidence does not contain sufficient details to confirm or refute whether consumers can obtain a comprehensible explanation of the creditworthiness assessment. Key contents that could clarify this finding are not visible in the provided evidence.

Model recommendation:

Implement a mechanism for clear and comprehensible explanations of automated creditworthiness assessments to consumers.

### 9. Incomplete Evidence for Group Disparity Assessment

- Finding: `finding_90afe8c483434a7aa830cd7476454d8d`
- Control: `model:CREDIT-SUBGROUP-DISPARITY`
- Severity: MEDIUM
- Verification: `supported=null`
- Applicability: undetermined

Model observation:

There is no evidence presented to assess whether performance or selection rates diverge across groups defined by sensitive attributes.

Verification rationale:

The evidence for assessing group disparity in performance or selection rates across defined sensitive attributes is incomplete, as the underlying records and content are unavailable, including crucial evidence regarding the measures and analysis of such attributes.

Model recommendation:

Analyze subgroup performance or selection rates across protected attributes to ensure there are no disparities.

### 10. Inadequate traceable record for auditing purposes

- Finding: `finding_54389eec66884b0090ea5ad44e92f65e`
- Control: `model:EU-AI-ACT-ART-12`
- Severity: HIGH
- Verification: `supported=null`
- Applicability: undetermined

Model observation:

The events logged in the audit report do not provide a comprehensive traceable record that aligns with the purpose of the system, as required by Article 12 of the EU AI Act.

Verification rationale:

The audit evidence does not fully substantiate whether the logging system captures all relevant events as required by Article 12 of the EU AI Act. While there is documentation of activities and oversight, the extent to which it meets the traceability requirements cannot be assessed without the complete log details and contents associated with the activities. Hence, the evidence neither supports nor refutes the finding without access to the omitted records.

Model recommendation:

Enhance the logging system to ensure it captures all relevant events in a manner that meets the traceability requirements outlined in Article 12.

### 11. Unclear human oversight capabilities

- Finding: `finding_943f699a543e4890849690feb3ca19fd`
- Control: `model:EU-AI-ACT-ART-14`
- Severity: MEDIUM
- Verification: `supported=null`
- Applicability: undetermined

Model observation:

The activity profile states that a Lead Data Scientist and Credit Risk Manager may intervene, but the evidence does not clearly demonstrate their effective oversight of the operational activities or the ability to intervene as necessary, as required by Article 14 of the EU AI Act.

Verification rationale:

The candidate finding claims that there is a lack of clear evidence demonstrating effective oversight by the Lead Data Scientist and Credit Risk Manager. The available evidence shows that these roles can intervene and must review artifacts before downstream activities, but it does not definitively conclude whether their oversight was effectively demonstrated during the operational activities. Important content that could clarify this aspect is missing from the visible evidence, preventing a definitive judgment on the finding.

Model recommendation:

Clarify and document the specific oversight measures that allow for intervention and stopping of the operational activities.

### 12. Insufficient technical documentation

- Finding: `finding_2d2d88b8081a4ce6a13b23146036061c`
- Control: `model:EU-AI-ACT-ART-11`
- Severity: HIGH
- Verification: `supported=null`
- Applicability: undetermined

Model observation:

The produced artifacts and documentation do not sufficiently cover the system's design, capabilities, limitations, and monitoring as required by Article 11 of the EU AI Act.

Verification rationale:

Artifact content is not available to the verifier; an artifact registration and digest alone cannot support a verdict.

Model recommendation:

Prepare and maintain comprehensive technical documentation that fulfills the requirements specified in Article 11 and Annex IV, ensuring it is up-to-date.

### 13. Use of Sensitive Attributes in Analysis

- Finding: `finding_b997dd6f2d4746d7a0c24db92c350183`
- Control: `model:CREDIT-PROTECTED-ATTRIBUTE`
- Severity: HIGH
- Verification: `supported=null`
- Applicability: applicable

Model observation:

The dataset includes sensitive attributes such as age and personal status (sex) that are used in the analysis, potentially violating the prohibition against using special categories of personal data.

Verification rationale:

The candidate finding states that sensitive attributes such as age and personal status (sex) are used in the analysis, potentially violating data protection regulations. However, while the visibility of sensitive attributes is acknowledged in the description of the activity profile, there is no explicit evidence provided in the events that substantiates or refutes the actual use of these attributes in a manner that could lead to a regulatory violation. The evidence as presented does not allow for a definitive assertion, thus making the finding undetermined in this current context.

Model recommendation:

Review the use of sensitive attributes in the dataset and ensure compliance with data protection regulations.

### 14. Lack of Automated Assessment Explainability

- Finding: `finding_bf041b251c424920beaf9c2078849d3e`
- Control: `model:CREDIT-ADVERSE-ACTION`
- Severity: HIGH
- Verification: `supported=null`
- Applicability: undetermined

Model observation:

The evidence does not clearly indicate whether consumers can obtain a comprehensible explanation of the creditworthiness assessments or if they have the right to contest an automated decision.

Verification rationale:

The candidate finding concerning explainability and the right to contest automated decisions references a lack of clarity in the evidence regarding whether consumers are informed about the creditworthiness assessment process. There is no visible evidence in the current audit evidence that conclusively addresses whether such explanations are provided or the rights afforded to consumers in relation to automated decisions. Therefore, without the necessary evidence to determine this, the supported status is marked as null.

Model recommendation:

Implement procedures to ensure consumers receive explanations for automated decisions and the right to contest those decisions.

### 15. Unassessed Group Performance Disparity

- Finding: `finding_26655e0c7d3a4afbbc2e16a0c49fbb3a`
- Control: `model:CREDIT-SUBGROUP-DISPARITY`
- Severity: MEDIUM
- Verification: `supported=null`
- Applicability: undetermined

Model observation:

There is no evidence presented to assess whether performance or selection rates diverge across groups defined by sensitive attributes such as age or personal status (sex).

Verification rationale:

The evidence does not provide sufficient content to assess the performance or selection rates across groups defined by sensitive attributes, which is the focus of the finding. Specifically, the audit evidence events are redacted, and no concrete performance metrics are visible to validate or refute the claim about group disparities. Therefore, it remains undetermined whether the audit finding is supported or not.

Model recommendation:

Conduct a thorough examination of performance metrics across different demographic groups to identify and address any disparities.

### 16. Incomplete and Non-Comprehensive Logs

- Finding: `finding_04f73c3b8bd4453c8d71780aad94e14c`
- Control: `model:EU-AI-ACT-ART-12`
- Severity: HIGH
- Verification: `supported=null`
- Applicability: undetermined

Model observation:

The events logged in the audit report do not provide a comprehensive traceable record that aligns with the purpose of the activity, specifically for assessing the historical dataset. The evidence states, "The events logged in the audit report do not provide a comprehensive traceable record that aligns with the purpose of the activity."

Verification rationale:

A required cited record cannot fit as a complete JSON record.

### 17. Insufficient Evidence of Effective Human Oversight

- Finding: `finding_fb81a767b8c34fd299021a93466aab58`
- Control: `model:EU-AI-ACT-ART-14`
- Severity: MEDIUM
- Verification: `supported=null`
- Applicability: applicable

Model observation:

The activity profile indicates that a Lead Data Scientist and Credit Risk Manager may intervene, but the evidence does not demonstrate the actual capability for intervention or decision-making regarding the ongoing activity or its outputs. The profile states that oversight is limited to review before downstream feature engineering or model use.

Verification rationale:

The evidence does not provide concrete visibility into any actual human intervention or decision-making related to the activity profile. Review by a Lead Data Scientist or Credit Risk Manager is mentioned but not substantiated by direct, observable actions in the current scope. The evidence required to support or refute the claim of effective human oversight is missing or unavailable.

### 18. Lack of Sufficient Technical Documentation

- Finding: `finding_c26aed118b4d4ec58e01436c94b3a638`
- Control: `model:EU-AI-ACT-ART-11`
- Severity: HIGH
- Verification: `supported=null`
- Applicability: applicable

Model observation:

The produced artifacts and documentation do not adequately cover the system’s design, capabilities, limitations, and monitoring as required for high-risk AI systems. Previous findings noted, "The output does not contain sufficient technical documentation that meets the requirements outlined in Article 11."

Verification rationale:

The evidence regarding the produced artifacts and documentation pertaining to the system's design, capabilities, limitations, and monitoring is not present. Therefore, it's impossible to determine if the artifacts and documentation adequately cover these aspects, which are prerequisite for assessing the claim about insufficient technical documentation as required by Article 11 for high-risk AI systems.

### 19. Use of Sensitive Attributes in Analysis

- Finding: `finding_e5c4c5917cad4b19b4dc20df3d5cdac5`
- Control: `model:CREDIT-PROTECTED-ATTRIBUTE`
- Severity: HIGH
- Verification: `supported=null`
- Applicability: applicable

Model observation:

The dataset includes sensitive attributes such as age and personal status (sex) that are used in the analysis. The audit indicates that the handling may conflict with GDPR requirements since these are treated as sensitive data that should not be included in the assessment according to Directive (EU) 2023/2225, Article 18(3)-(4).

Verification rationale:

The available evidence does not contain the necessary details about the handling of sensitive attributes related to GDPR compliance. While it acknowledges that the dataset includes sensitive attributes like age and personal status (sex), it does not provide sufficient information to determine if they were excluded from the analysis. Therefore, I cannot conclusively support or refute the finding.

Model recommendation:

Ensure that sensitive attributes are excluded from the data analysis for creditworthiness assessment.

### 20. Lack of Clear Explanation and Review Rights

- Finding: `finding_c8b892d155424d3cb3987b7a343e359d`
- Control: `model:CREDIT-ADVERSE-ACTION`
- Severity: HIGH
- Verification: `supported=null`
- Applicability: undetermined

Model observation:

The evidence does not clearly indicate that consumers can obtain a comprehensible explanation of the creditworthiness assessment process, including its logic and risks. Users must be able to express their viewpoint, review the decision, and contest the automated processing as stipulated in Directive (EU) 2023/2225, Article 18(8)-(9).

Verification rationale:

The evidence does not directly substantiate nor refute the claim about the lack of clear explanations for automated credit decisions and consumer contestation rights, as the necessary content or records detailing the consumer's ability to understand the assessment process are not available within the provided audit evidence. There are multiple referenced events that might contain pertinent details, but without access to their content, it is not possible to validate the finding definitively.

Model recommendation:

Implement mechanisms to ensure that consumers receive a clear explanation of automated credit decisions and provide means for contesting these decisions.

### 21. Lack of Evidence on Selection Rate Disparities

- Finding: `finding_fd4960edb6c24aacab575f49c4e74165`
- Control: `model:CREDIT-SUBGROUP-DISPARITY`
- Severity: MEDIUM
- Verification: `supported=null`
- Applicability: applicable

Model observation:

There is no evidence presented to assess whether performance or selection rates diverge across groups defined by sensitive attributes (age, personal status). This absence prevents ruling out potential disparities in credit decisions based on these characteristics.

Verification rationale:

To determine whether the finding regarding performance or selection rates diverging across groups defined by sensitive attributes is supported, specific evidence of performance or selection rates across these diverse demographic groups is required. However, the audit evidence does not provide any event records or content directly addressing these metrics. Therefore, without this vital information, it is indeterminate whether the claim can be substantiated or refuted.

Model recommendation:

Conduct an analysis that specifically evaluates performance and selection rates across different demographic groups to identify any disparities.

### 22. Insufficient Event Logging for Compliance

- Finding: `finding_78be4547e82d43fdbb19cefd1bdd22a2`
- Control: `model:EU-AI-ACT-ART-12`
- Severity: HIGH
- Verification: `supported=null`
- Applicability: undetermined

Model observation:

The events logged in the audit report do not provide a comprehensive traceable record that aligns with the purpose of the activity; hence it fails to meet the record-keeping requirements of Article 12. Relevant logged events do not demonstrate a complete history of actions taken during the run.

Verification rationale:

The evidence is insufficient to determine whether the events logged meet the record-keeping requirements of Article 12, as the artifact content is not visible. Therefore, evaluating the comprehensiveness of the recorded events is not possible.

Model recommendation:

Improve logging practices to ensure a comprehensive record of all actions and events for compliance with record-keeping requirements.

### 23. Lack of Effective Human Oversight Evidence

- Finding: `finding_762a488231e9410fae8e00b9029115cf`
- Control: `model:EU-AI-ACT-ART-14`
- Severity: MEDIUM
- Verification: `supported=null`
- Applicability: applicable

Model observation:

The activity profile indicates that human oversight is possible by a Lead Data Scientist and Credit Risk Manager, however, there is insufficient evidence to confirm whether the human oversight measures are being effectively applied or if interventions were made during the run.

Verification rationale:

The candidate finding notes potential human oversight by a Lead Data Scientist and Credit Risk Manager but claims insufficient evidence to confirm whether those oversight measures were effectively applied. However, the provided evidence includes several risk assessments and activity profile recordings with the involvement of a system actor, yet it lacks explicit confirmations of oversight interventions by the human actors mentioned, such as user behavior regarding human oversight actions. Consequently, it is undetermined whether effective oversight occurred, as the evidence does not affirm or refute it; the required content is not visible in this projection.

Model recommendation:

Enhance documentation to capture instances of human oversight interventions in future runs.

### 24. Inadequate Technical Documentation

- Finding: `finding_ed5abf76b89d42d0aa4acca2bc66fe47`
- Control: `model:EU-AI-ACT-ART-11`
- Severity: HIGH
- Verification: `supported=null`
- Applicability: undetermined

Model observation:

The produced artifacts and documentation do not adequately cover the system's design, capabilities, limitations, and performance monitoring as required under Article 11 of the AI Act. The existing documentation fails to provide sufficient detail for assessment.

Verification rationale:

Artifact content is not available to the verifier; an artifact registration and digest alone cannot support a verdict.

Model recommendation:

Expand technical documentation to include a comprehensive overview of the system's design, capabilities, limitations, and monitoring.


## Recorded human decision and completion

The user approved this exact review. Authenticated nonautomatic approval is recorded at event
587, and terminal completion at 591–592 retains all 24 finding IDs and this context hash.
The Run is COMPLETED with approved human review, 593 valid events and no artifact manifest error.
See `real-structural-continuation-final.json` for the final proof and retry-history limitation.
