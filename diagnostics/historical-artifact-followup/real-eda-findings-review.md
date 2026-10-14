# Descriptive EDA findings review

Run: `run_c0dd948eb5b74380993cc014cf8c51b6`. Decision: `decision_9903201ea1214cea8b25cf2e603c1834` (CR-102). Audit completed at event 682; review requested at 684.

The EDA generated a strict-JSON profile and two PNGs. The task completed without missing required artifacts, unresolved tool calls or validation failures. A18 passes. No deterministic control failed and no finding is CRITICAL.

The audit reports 16 HIGH and 8 MEDIUM model-proposed findings. All 24 applicability assessments have `supported=null`; they remain uncertain, rather than confirmed violations. Their recommendations do not authorize implementation or downstream credit decisions. The recorded scope is bounded local descriptive profiling, without model training or live credit decisions.

The exact review binds report, activity profile v14, all completed tool calls, capability metadata and artifact hashes. Context SHA: `0b53cf90dc65620b93a1ae54121e37bb4cc06339be1b9a455dc40b4598ae7c27`. Approval applies to this Run and audit snapshot. It supplies no general credit-use or future-task authorization.

| Model control | Severity | Count | Verification |
|---|---|---:|---|
| model:CREDIT-PROTECTED-ATTRIBUTE | HIGH | 4 | uncertain (`supported=null`) |
| model:CREDIT-ADVERSE-ACTION | HIGH | 4 | uncertain (`supported=null`) |
| model:CREDIT-SUBGROUP-DISPARITY | MEDIUM | 4 | uncertain (`supported=null`) |
| model:EU-AI-ACT-ART-12 | MEDIUM | 4 | uncertain (`supported=null`) |
| model:EU-AI-ACT-ART-14 | HIGH | 4 | uncertain (`supported=null`) |
| model:EU-AI-ACT-ART-11 | HIGH | 4 | uncertain (`supported=null`) |

## Exact proposed findings

### Handling of Sensitive Attributes

- ID: `finding_4128e6ea1f394b589c00894cadc9310b`
- Control: `model:CREDIT-PROTECTED-ATTRIBUTE`; severity: HIGH

The analysis revealed that sensitive attributes, including age and personal status, are included in the dataset. However, GDPR special-category data was not explicitly checked or excluded. The handling of such sensitive information may not meet the requirement that "information must not include the special categories of personal data in GDPR Article 9(1)," as the dataset includes attributes that can be considered sensitive.

Recommendation: Review the dataset to ensure compliance with GDPR Article 9(1) and remove any special-category data. Additionally, the model's input features should exclude sensitive attributes that could lead to bias or discrimination in lending decisions.

### Consumer Rights in Automated Processing

- ID: `finding_f8ae8fb8411242c6a56ba9d487a52d6e`
- Control: `model:CREDIT-ADVERSE-ACTION`; severity: HIGH

The evidence does not demonstrate that consumers can review the automated assessment, express a viewpoint, or contest automated credit decisions. Directive (EU) 2023/2225 mandates such processes for consumers in the context of automated processing.

Recommendation: Implement mechanisms to ensure that consumers can obtain clear explanations of automated creditworthiness assessments, including the logic and significance of those assessments, along with ensuring their right to a human review.

### Lack of Subgroup Performance Analysis

- ID: `finding_d5d0208dfe9b480daa20f173fb95b003`
- Control: `model:CREDIT-SUBGROUP-DISPARITY`; severity: MEDIUM

The evidence does not provide insights into performance or selection rates across different subgroups defined by sensitive attributes. Without this data, it cannot be established whether there is a material disparity in lending outcomes based on protected characteristics such as age or personal status.

Recommendation: Ensure that data analytics include a breakdown of performance metrics across different demographic groups to identify and address any potential biases.

### Insufficient Traceability of Logged Events

- ID: `finding_b7b56a9c24c04c66a106f86b2ea6aad6`
- Control: `model:EU-AI-ACT-ART-12`; severity: MEDIUM

The evidence shows that events are logged. However, it does not ensure that the logging capabilities provide traceability appropriate to the intended purpose of the system as required by Article 12.

Recommendation: Enhance logging capabilities to ensure a higher level of traceability appropriate to the system's intended purpose.

### Lack of Effective Human Oversight Mechanisms

- ID: `finding_162713fecdef438480925e78b7930119`
- Control: `model:EU-AI-ACT-ART-14`; severity: HIGH

While the activity profile indicates that human oversight is allowed, there is no explicit evidence demonstrating the ability of humans to intervene, override, or stop the ongoing work, as required by Article 14.

Recommendation: Implement and document clear mechanisms for human intervention or oversight during system operations.

### Missing Technical Documentation for Compliance Assessment

- ID: `finding_856641f9e0fc4afa8e18082fab70ab9e`
- Control: `model:EU-AI-ACT-ART-11`; severity: HIGH

The evidence does not demonstrate that technical documentation has been properly drawn up and kept up to date, which is necessary to assess compliance according to Article 11.

Recommendation: Prepare and maintain the necessary technical documentation as outlined in Article 11 and Annex IV.

### Inclusion of Sensitive Attributes in the Dataset

- ID: `finding_dbb0edfea9804c029f0e20fdcf7e22e2`
- Control: `model:CREDIT-PROTECTED-ATTRIBUTE`; severity: HIGH

The analysis revealed that sensitive attributes, including age and personal status, are included in the dataset. This is supported by the audit context stating that "Age, personal_status_sex and foreign_worker are sensitive or protected attributes in this dataset."

Recommendation: No additional recommendation recorded.

### Lack of Rights for Affected Consumers

- ID: `finding_d7cd1bb71adc4951890d781c56bc68c2`
- Control: `model:CREDIT-ADVERSE-ACTION`; severity: HIGH

The evidence does not demonstrate that consumers can review the automated assessment, express a viewpoint, or contest an automated credit decision.

Recommendation: No additional recommendation recorded.

### Insufficient Data on Group Performance Disparancy

- ID: `finding_9ef468939d6548a8861e601aac7a024a`
- Control: `model:CREDIT-SUBGROUP-DISPARITY`; severity: MEDIUM

The evidence does not provide insights into performance or selection rates across different subgroups defined by sensitive attributes.

Recommendation: No additional recommendation recorded.

### Insufficient Traceability of Event Logging

- ID: `finding_4b8abb247d4345588711bcc133a49831`
- Control: `model:EU-AI-ACT-ART-12`; severity: MEDIUM

The logs record events, but they do not ensure the level of traceability appropriate to the intended purpose, as required by Article 12 of the AI Act. It remains unclear if the logging capabilities can support identifying situations that may present a risk. For example, "235 events" are logged, but the integrity and traceability specifics remain undemonstrated.

Recommendation: No additional recommendation recorded.

### Unclear Evidence of Human Oversight

- ID: `finding_0852de16e22e4df0a6143f60e7e642b2`
- Control: `model:EU-AI-ACT-ART-14`; severity: HIGH

While the activity profile indicates the presence of human oversight, evidenced through the ability of a Lead Data Scientist and Credit Risk Manager to stop or reject work, there is no explicit evidence demonstrating the effectiveness of this oversight during the operational use of the system. Specifically, the lack of concrete instances where the oversight was exercised raises concerns.

Recommendation: No additional recommendation recorded.

### Technical Documentation Not Properly Maintained

- ID: `finding_0b75c198f1e14957b83f3aa4500fb76e`
- Control: `model:EU-AI-ACT-ART-11`; severity: HIGH

The evidence does not show that the technical documentation required for compliance has been properly drawn up and kept up-to-date as mandated by Article 11 of the AI Act. Without this documentation, authorities cannot adequately assess conformity. The lack of an artifact that demonstrates compliance with the documentation requirements implies significant gaps.

Recommendation: No additional recommendation recorded.

### Sensitive Attributes Present in Dataset

- ID: `finding_c8f3868d8fa348c78ba36dc603e14cba`
- Control: `model:CREDIT-PROTECTED-ATTRIBUTE`; severity: HIGH

The analysis revealed that sensitive attributes, including age and personal status, are included in the dataset. This is a violation of the requirements outlined in Directive (EU) 2023/2225, Article 18(3)-(4) which mandates that information must not include special categories of personal data from GDPR Article 9(1).

Recommendation: Exclude sensitive attributes from the dataset prior to any analysis or model training.

### Lack of Explanation for Automated Assessment

- ID: `finding_4b0d85a16b374f2794e0f2e0a17c45df`
- Control: `model:CREDIT-ADVERSE-ACTION`; severity: HIGH

The evidence does not demonstrate that consumers can review the automated assessment, express a viewpoint, or contest a credit decision. This does not comply with the requirements of Directive (EU) 2023/2225, Article 18(8)-(9), which mandates a right to human intervention and a clear explanation of the assessment.

Recommendation: Implement a system for providing clear explanations and a formal process for consumers to contest automated assessments.

### No Insights into Subgroup Performance

- ID: `finding_42b617a2972545ca8e3bd87910e87aa1`
- Control: `model:CREDIT-SUBGROUP-DISPARITY`; severity: MEDIUM

The evidence does not provide insights into performance or selection rates across different subgroups defined by sensitive attributes. This absence makes it impossible to assess whether there is any unexplained disparity, which is required under the applicable regulations.

Recommendation: Conduct an analysis of model performance across different subgroups to assess potential disparities.

### Inadequate Traceability in Logs

- ID: `finding_ccbbb207ae944559b80b0e15839c66b9`
- Control: `model:EU-AI-ACT-ART-12`; severity: MEDIUM

The logs record events, but they do not ensure the level of traceability appropriate to the intended purpose of exploratory analysis, as required under Article 12 of the EU AI Act.

Recommendation: Enhance logging mechanisms to improve event traceability for the specific purposes of exploratory analysis.

### Insufficient Human Oversight Evidence

- ID: `finding_83963fb5e3874547a0758606b5d0b1e5`
- Control: `model:EU-AI-ACT-ART-14`; severity: HIGH

While the profile indicates the presence of human oversight, detailed events demonstrate that oversight did occur, with human intervention allowed, but the actions of the Lead Data Scientist and Credit Risk Manager are not fully corroborated by evidence of real-time intervention or stopping the process.

Recommendation: Document specific instances of oversight interventions to ensure compliance with the oversight requirements.

### Incomplete Technical Documentation

- ID: `finding_3926857be1a94fa4af287b4ad20abfb8`
- Control: `model:EU-AI-ACT-ART-11`; severity: HIGH

The evidence does not demonstrate that technical documentation has been properly drawn up and kept up to date, which is necessary for compliance under Article 11 of the EU AI Act.

Recommendation: Ensure all technical documentation is drawn up comprehensively and updated regularly to reflect current system capabilities and limits.

### Inclusion of Sensitive Attributes in Dataset

- ID: `finding_ba7dcc5d7bf543d89f594fdec4c37cb4`
- Control: `model:CREDIT-PROTECTED-ATTRIBUTE`; severity: HIGH

The analysis revealed that sensitive attributes, including age and personal status, are included in the dataset. This is contrary to Directive (EU) 2023/2225 which specifies that information must not include the special categories of personal data as outlined in GDPR Article 9(1).

Recommendation: Remove sensitive attributes from the dataset or ensure they are not used in the assessment.

### Lack of Consumer Rights in Automated Processing

- ID: `finding_6d910293cc0a4a36931de3698206bb72`
- Control: `model:CREDIT-ADVERSE-ACTION`; severity: HIGH

The evidence does not demonstrate that consumers can review the automated assessment, express a viewpoint, or contest an automated credit decision as required by Directive (EU) 2023/2225.

Recommendation: Implement processes to ensure consumers can contest automated decisions and receive clear explanations of assessments.

### Lack of Insights on Subgroup Performance and Selection

- ID: `finding_5b4e217b3c7b4edda2cd7472a8792b55`
- Control: `model:CREDIT-SUBGROUP-DISPARITY`; severity: MEDIUM

The evidence does not provide insights into performance or selection rates across different subgroups defined by sensitive attributes such as age or sex, indicating potential for undisclosed disparities.

Recommendation: Conduct a detailed analysis to evaluate performance and selection rates across subgroups.

### Insufficient Traceability in Log Records

- ID: `finding_ec59ce9f8b424f31a37fbfa8236078fa`
- Control: `model:EU-AI-ACT-ART-12`; severity: MEDIUM

The logs record events, but they do not ensure the level of traceability appropriate to the intended purpose of exploratory analysis, as indicated in the prior candidate finding and the request for a more comprehensive logging strategy.

Recommendation: Enhance the logging mechanism to ensure it captures adequate details for effective traceability related to the exploratory analysis of the dataset.

### Lack of Effective Human Oversight

- ID: `finding_c3ec5e7a0820414c9105ae14af7a6f56`
- Control: `model:EU-AI-ACT-ART-14`; severity: HIGH

While the profile indicates the presence of human oversight (Lead Data Scientist and Credit Risk Manager), a review of the logs does not adequately demonstrate that these individuals could effectively intervene in or stop the automated assessment process. The approval recorded does not provide evidence of actual oversight during the operational use.

Recommendation: Document specific instances of human intervention in the process and establish clearer oversight measures that enable real-time decision-making.

### Missing Proper Technical Documentation

- ID: `finding_45b0f105a21f4a4db2412a6a220b1969`
- Control: `model:EU-AI-ACT-ART-11`; severity: HIGH

The evidence does not demonstrate that the technical documentation required for compliance has been properly drawn up and kept up to date — a critical factor for authorities to assess conformity.

Recommendation: Ensure that comprehensive technical documentation is created and regularly updated to reflect the current system's compliance and operational status.
