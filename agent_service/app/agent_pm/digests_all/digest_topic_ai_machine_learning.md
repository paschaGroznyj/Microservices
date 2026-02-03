# 📋 Дайджест материалов внешних источников за период/по теме: 'Сформируй дайджест по статьям связанным с AI machine learning'
**Создан:** 28.01.2026 12:28
**Найдено статей:** 4

## 🎯 Industry-Aligned_Granular_Topic_Modeling
**Категория:** None | **Дата:** 2026-01-16
**ТЕМА:** ['Computation and Language', 'Artificial Intelligence', 'Machine Learning']
## Ссылка на источник https://arxiv.org/abs/2601.11762

### Саммари статьи
30e5
Industry-Aligned Granular Topic Modeling
Sae Young Moon1, Myeongjun Erik Jang1, Haoyan Luo1, Chunyang Xiao1,
Antonios Georgiadis1,Fran Silavong1
1J.P. Morgan Chase
{saeyoung.moon, myeongjun.jang, haoyan.luo, chunyang.xiao,
antonios.georgiadis, fran.silavong} @jpmchase.com
Abstract
Topic modeling has extensive applications in
text mining and data analysis across various
industrial sectors. Although the concept of
granularity holds significant value for business
applications by providing deeper insights, the
capability of topic modeling methods to pro-
duce granular topics has not been thoroughly
explored. In this context, this paper introduces
a framework called TIDE, which primarily pro-
vides a novel granular topic modeling method
based on large language models (LLMs) as a
core feature, along with other useful functional-
ities for business applications, such as summa-
rizing long documents, topic parenting, and
distillation. Through extensive experiments
on a variety of public and real-world business
datasets, we demonstrate that TIDE’s topic
modeling approach outperforms modern topic
modeling methods, and our auxiliary compo-
nents provide valuable support for dealing with
industrial business scenarios. The TIDE frame-
work is currently undergoing the process of
being open sourced.
1 Introduction
Topic modeling is a Natural Language Process-
ing ( NLP) technique designed to discover mean-
ingful topics within a corpus, which has a broad
range of practical usage for text mining and data
analysis in research and industry (Ranaei et al.,
2017; Asmussen and Møller, 2019; Xiong et al.,
2019). From a business perspective, granularity
— referring to the breakdown of a concept into
smaller, more detailed parts (Mulkar-Mehta et al.,
2011) — is an important consideration. Higher
levels of granularity provide more informative in-
sights (Howald and Abramson, 2012), offering ad-
vantages for analysis and greater control over data.
In this context, the ability to provide granular top-
ics is a highly desirable attribute of topic modeling
for business use-cases. To confirm this as a prelim-
inary study, in this work, we conduct an internalstudy with our business insight analysts where ana-
lysts choose their preferred topics amongst topics
of different levels of granularity. Our results show
that the business preferred medium to high levels
of granularity 92% of the time.1
Despite its practical significance, the generation
of granular topics has not received much attention.
Most previous topic modeling studies have concen-
trated on discovering more coherent and diverse
topics (Grootendorst, 2022; Pham et al., 2024; Lam
et al., 2024; Angelov and Inkpen, 2024), as well as
on automatic topic naming (Alokaili et al., 2020).
Given the objectives of these studies, the topic mod-
elling approaches have predominantly concentrated
on higher-level concepts and have been assessed us-
ing classification datasets, which generally contain
10-20 high-level topics. Consequently, granular
topic modeling remains an underexplored area that
requires further investigation, given its importance
in business applications.
In the light of this, we introduce a frame-
work called TIDE (TopicInsights andDocument
Exploration) that not only supports a novel large
language model ( LLM ) based granular topic mod-
eling approach but also offers various functions that
facilitates better usability in real business use-cases,
involving a targeted summarization, topic hierar-
chy detection, distillation, and automatic evalua-
tion tool. Subsequently, we evaluate TIDE using
three public datasets that are well-suited for granu-
lar topic modeling evaluation, as well as two real-
world business datasets we encountered. Our ex-
perimental results demonstrate that TIDE is highly
competent in granular topic modeling, surpass-
ing modern topic modeling baseline approaches.
While the significant variability is observed in tra-
ditional methods across various public datasets,
TIDE consistently achieves high rankings regard-
1Details of the internal study are presented in Ap-
pendix A.1.
1arXiv:2601.11762v1  [cs.CL]  16 Jan 2026less of dataset. Also, it offers various practical
advantages for enhancing industrial use cases.
2 TIDE Framework
In this section, we present the TIDE framework,
which has been developed as a Python library. Fig-
ure 2 illustrates the overall TIDE architecture: with
granular topic modelling at its core, along with sup-
ports for summarization for long documents and
distillation for inference based on topic modelling
output. More detailed figure with a specific exam-
ple is presented in Figure E in Appendix. LLMs
are used in various library components and the
prompts employed are detailed in Appendix B.1
through B.4.
2.1 Summarization
We provide a targeted summarization module
for datasets with lengthy, noisy, and complex-
structured input texts, such as conversations logs or
news articles, which are commonly encountered in
real-business contexts. For such long and compli-
cated texts, a pre-procesing step is required because
the texts may exceed the LLM s’ context length,
or more subtly, exceed the LLM s’ effective con-
text length (Hsieh et al., 2024; Liu et al., 2024b).
Additionally, preparing clean, concise, and well-
structured text prior to topic modeling can alleviate
LLM s’ performance issues associated with lengthy
input contexts (Liu et al., 2024a). Although trun-
cation can be considered, summarization is more
desirable because it can retain key content relevant
for topic generation. Furthermore, our summariza-
tion module employs business’ definition of desired
topics to focus the summary for downstream topic
modeling. For example, given customer-chatbot
conversations, we can summarize both content that
is explicitly mentioned (e.g. contact reason) and
content that can be inferred implicitly (e.g. chat-
bot behavioral issues). This allows TIDE to derive
business-relevant topics insights on both explicit
and implicit content, enhancing usefulness to in-
dustry use-cases. The design of the summarization
prompt is outlined in Appendix B.1.
2.2 Granular Topic Modeling
TIDE topic modeling provides business-relevant,
granular topics through a guided cluster-then-
generate approach. First, TIDE clusters semanti-
cally similar texts using K-means clustering, which
is a key for generating granular topics. Without
clustering, LLM s will identify holistic features andare inclined to produce higher-level concepts. Con-
versely, clustering enables LLM s to access detailed
information that might be considered insignificant
in the perspective of the entire corpus and thus
overlooked. Subsequently, each cluster of texts
is provided to the LLM to:(1) generate distinct,
granular topic(s), and (2) accurately assign text to
the appropriate topic(s). During topic generation,
the clustering step is beneficial for several reasons.
Firstly, it minimizes the LLM costs. Secondly, by
providing LLM with similar texts, TIDE allows the
LLM to generate topics that generalize across the
input texts so that the topics have the granularity
tailored to the dataset. Additionally, TIDE can op-
tionally incorporate business’ definition of desired
topics to maximize the usefulness for users (refer to
Appendix D for examples of business’ definitions).
When it comes to topic modeling, TIDE appears
to follow procedures similar to LLooM (Lam et al.,
2024). However, TIDE differs from LLooM in sev-
eral aspects, mainly in topic assignment process.
First, LLooM scores each (document,topic) pair,
and then allocates a topic with the highest score to
the respective document. This process is resource-
intensive and slow, as LLM s are invoked O(N×k)
times, where Nandkare the number of documents
and the number of generated topics, respectively.
Instead, TIDE assigns topics immediately after the
clustering phase using a single LLM call for each
cluster. Consequently, TIDE reduces the number
ofLLM calls to O(N) , enabling more resource-
efficient topic assignment process. Second, the
topic assignment process is optional in LLooM.
However, we found that the topic assignment is a
crucial feature in business use cases2, which led us
to include the topic assignment process as a manda-
tory step. Third, before conducting topic modeling,
LLooM preprocesses all documents via extractive
summarization to capture their main gists, followed
by abstractive summarization. However, we em-
pirically found that allowing the LLM s to generate
summaries or topics from content rich in details
is crucial. Consequently, TIDE directly applies
abstractive summarization to lengthy documents
and recommends using raw inputs for shorter doc-
uments. Finally, similar to topicGPT (Pham et al.,
2024), TIDE incorporates a refinement module
which tries to merge small clusters into prominent
ones once all the topics are generated; LLooM does
2Clients often request 1) access to the exact percentage of
each topic 2) a manual examination of (document,topic) pairs.
2Figure 1: TIDE architecture diagram
not conduct refinement.
2.3 Topic Hierarchy Detection
A user might find the generated topics to be ex-
cessively granular or may want to construct a hi-
erarchical structure of the topics. TIDE provides
further functionalities for identifying the topolog-
ical relationships of granular topics. The cluster-
then-generate step introduced in section 2.2 can be
re-purposed to group similar granular topics into
hierarchies. Taking the generated granular topics as
inputs, the same algorithm performs cluster-then-
generate using the prompt detailed in Appendix B.4.
After this process, for example, granular topics ‘Re-
quest for overdraft fee refund’ and ‘Request for late
fee refund’ can be grouped under parent topic ‘Re-
fund request’.
2.4 Distillation
Once topics are generated and assigned, most fu-
ture documents can be classified into these existing
topics, unless there is a change in the underlying
topics (Jang et al., 2024). To facilitate cost effec-
tive classification, TIDE supports distillation by
fine-tuning a pre-trained model (e.g. BERT) to
learn to classify data into existing topics. Given
that topics may originate from datasets with limited
data, resulting in very few records per class, TIDE
supports distillation using SetFit (Tunstall et al.,
2022), which has proven to be effective to learn
classifications in situation of data scarcity.
2.5 Automatic Evaluation Tool
Assessing the quality of generated topics following
topic modeling is a natural step. However, most
real-world data lacks labels, which hinders perfor-
mance evaluation through comparison with ground-
truth. In this regard, TIDE offers an automaticDataset Banking77 Bills Wiki
### low-level topics 77 114 279
Avg. # of words per text 11 184 3K
Table 1: The basic statistics of the public datasets.
evaluation tool utilizing a LLM , which measures
the following three metrics.
Label accuracy.Inspired by the work of Mu
et al. (2024), the label accuracy is designed to mea-
sure how distinct and appropriate the generated
topic is compared to other topics. For each topic,
we sample Ndocuments and identify the top- k
closest topics by using the embedding similarity.
Next, we obtain the percentage where a LLM re-
tains the originally assigned topic when compared
to the top- kclosest topics. Finally, the overall label
accuracy is calculated as the macro average across
all topics.
Topic accuracy/completeness.Motivated by re-
cent research using LLM s as judges (Zheng et al.,
2023; Verga et al., 2024; Gu et al., 2024), we di-
rectly inquire a LLM to assess the accuracy and
completeness of the generated topic considering
the original text on a scale ranging from 1 to 4. Our
tool enables the incorporation of business defini-
tions into evaluation prompts, aligning the judg-
ment bias of LLM with the perspective required by
businesses. The prompt designs of these metrics
are provided in Appendix B.6 and B.7.
3 Experiments on Public Datasets
3.1 Experiment Settings.
Datasets.To verify the competence of TIDE’s
granular topic modeling, we choose three pub-
lic datasets containing low-level topics, which are
well-suited for evaluating granular topic modeling:
3
0
x-amz-checksum-crc32:Jh1oNw==


------------------------------------------------------------------------------------------

## 🎯 Agentic_Business_Process_Management_Systems
**Категория:** None | **Дата:** 2026-01-25
**ТЕМА:** ['Artificial Intelligence', 'Software Engineering']
## Ссылка на источник https://arxiv.org/abs/2601.18833

### Саммари статьи
20a2
Agentic Business Process Management Systems
Marlon Dumas1, Fredrik Milani1, and David Chapela-Campa1
University of Tartu, Tartu, Estonia
{marlon.dumas,fredrik.milani,david.chapela}@ut.ee
Abstract.Since the early 90s, the evolution of the Business Process
Management (BPM) discipline has been punctuated by successive waves
of automation technologies. Some of these technologies enable the au-
tomation of individual tasks, while others focus on orchestrating the
execution of end-to-end processes. The rise of Generative and Agentic
Artificial Intelligence (AI) is opening the way for another such wave.
However, this wave is poised to be different because it shifts the focus
from automation to autonomy and from design-driven management of
business processes to data-driven management, leveraging process min-
ing techniques. This position paper, based on a keynote talk at the 2025
Workshop on AI for BPM, outlines how process mining has laid the foun-
dations on top of which agents can sense process states, reason about im-
provement opportunities, and act to maintain and optimize performance.
The paper proposes an architectural vision for Agentic Business Process
Management Systems (A-BPMS): a new class of platforms that inte-
grate autonomy, reasoning, and learning into process management and
execution. The paper contends that such systems must support a con-
tinuum of processes, spanning from human-driven to fully autonomous,
thus redefining the boundaries of process automation and governance.
Key words:Agentic Business Process Management, Automated Pro-
cess Execution, Autonomous Process Execution
1 Introduction
Over the past five decades, process-aware information systems [1] have evolved
from paper-driven systems supporting manual work and coordination into to-
day’s highly digitized and automated systems. Initially, workers relied on per-
sonal knowledge, paper documents, and individual judgment to complete tasks.
Early business process automation waves introduced tools like standard operat-
ing procedures, checklists, and forms, followed by spreadsheets and collaborative
software that organized work while still requiring human execution.
A leap occurred with the advent of case management and workflow man-
agement systems that could track work items and maintain digital records [2].
Soon after, Business Process Management Systems (BPMS) emerged, enabling
organizations to design process models separately from their execution using
notations like BPMN, while workflow engines automated routing between sys-
tems and people [3]. Parallel developments included business rules managementarXiv:2601.18833v1  [cs.AI]  25 Jan 20262 Marlon Dumas et al.
systems that separated decision-making logic from applications, enabling auto-
matic processing of routine decisions, and robotic process automation (RPA)
that mimicked human interactions with software applications via their user in-
terfaces [4]. Today’s generation of process execution systems integrate real-time
event processing, APIs, and machine learning to achieve higher levels of automa-
tion, combining execution with continuous monitoring and optimization [5].
The rise of Generative AI and Agentic AI are laying the ground for the next
evolution in this field, by enabling the development of tools capable not only
of executing predefined workflows, but also to dynamically generate new pro-
cess variations, make contextualized decisions, and autonomously adapt work-
flows based on changing conditions and objectives [6]. Agentic AI differs from
rule-based or script-based automation. Rule-based or script-based automation
technology, such as RPA, are characterized by their deterministic nature. They
follow a set of fixed, pre-defined rules. This capability makes them suitable for
automating repetitive tasks, for which all possible scenarios can be anticipated
and scripted. Agentic AI, on the other hand, can operate beyond a set of rules
by sensing and reasoning about the current state of processes, and triggering
next actions that align with performance objectives, within a set of constraints
(also known as a frame [6]).
Agents are software entities that have the capability to sense (understand
input), decide (analyse and determine what to do), and act (e.g. execute a task).
Hence, agentic AI can emulate a human resource [7]. In the context of business
process management and execution, agents can make use of process monitoring
and process mining tools to sense the state of the process in real-time. Based on
patterns extracted from historical executions, they can detect situations where
certain actions need to be triggered, even if these actions deviate from pre-
designed execution flows. By leveraging rule-based or script-based automation
tools, as well as automated planning techniques, AI agents can trigger actions
to drive the execution of one or more processes in an adaptive manner.
Inspired by an earlier definition of AI-Augmented Business Process Manage-
ment Systems[6], we define an Agentic Business Process Management System as
a class of process-aware information systems that leverages agentic AI technol-
ogy to enact business processes in such a way that: (1) the execution flows of a
process are not (fully) pre-determined via predesigned rules, models or scripts;
(2) adaptations to automated components of the process may not require explicit
changes to the supporting software applications; and (3) improvement opportu-
nities may be autonomously discovered, validated, and applied.
The rest of the paper outlines an architecture for an envisioned class of
process management and execution systems, namely Agentic BPM system (A-
BPMS). The paper argues that such systems need to support a range of pro-
cesses, with different levels of autonomy embedded in them, all the way from
largely manual or largely automated processes to almost-fully autonomous ones.Agentic Business Process Management Systems 3
Descriptive Process AnalyticsPredictive Process AnalyticsPrescriptive
Process OptimizationAgentic
BPM
OperationalTactical
Fig. 1.The Agentic BPM Pyramid: a classification of data-driven BPM approaches
(adapted from [8]).
2 From Process Mining to Agentic BPM Systems
Underpinning an Agentic Business Process Management System (A-BPMS) is
a collection of tools that allow for the transformation of data into insights and
decisions. This collection of data-driven techniques can be conceptualized in the
form of a pyramid of capabilities, depicted in Fig. 1. In this pyramid, each layer
builds on top of the layers below and serves as the basis for the ones above, and
comprises techniques pertaining to two use cases:i) tactical, aiming to inform
managers in their business process change decisions, typically with timeframes
of a few weeks to a few months between decision and change implementation;
andii) operational, aiming to issue recommendations or trigger actions in the
context of running cases, to improve their performance on a day-to-day basis.
The first layer,Descriptive Process Analytics, comprises techniques focused
on describing the current state of the process, generally addressing tactical use
cases. Within this layer, we find four main capabilities: (1)Automated process
discovery, aiming to discover process models from data, in order to exhibit the
behavior happening in the process, identify unexpected exceptions, and high-
light potential wastes. (2)Conformance checking, aiming to analyze the behavior
recorded in the event log against the behavior modeled by the process model, en-
abling the identification of deviations in the process and the comparison between
the designed and the real process. (3)Performance mining, aiming to analyze
one or more processes in terms of performance measures, such as cycle time
or cost per case. (4)Variant analysis, aiming to identify positive and negative
deviance in a process by comparing how the process is performed for different
subsets of cases – e.g., in different regions.
The second layer,Predictive process analytics, moves beyond describing the
current state to estimating the future state of the process. The capabilities within
this layer can be broadly classified into two categories: (1)What-if digital pro-
0
x-amz-checksum-crc32:tY9aoQ==


------------------------------------------------------------------------------------------

## 🎯 Confidence_over_Time_Confidence_Calibration_with_Temporal_Logic_for_Large_Language_Model_Reasoning
**Категория:** None | **Дата:** 2026-01-19
**ТЕМА:** ['Computation and Language', 'Machine Learning']
## Ссылка на источник https://arxiv.org/abs/2601.13387

### Саммари статьи
354c
Confidence over Time: Confidence Calibration with
Temporal Logic for Large Language Model Reasoning
Zhenjiang Mao1*, Anirudhh Venkat1*, Artem Bisliouk2, Akshat Kothiyal1
Sindhura Kumbakonam Subramanian1,Saithej Singhu1,Ivan Ruchkin1
1University of Florida
2University of Mannheim
Abstract
Large Language Models (LLMs) increasingly
rely on long-form, multi-step reasoning to solve
complex tasks such as mathematical problem
solving and scientific question answering. De-
spite strong performance, existing confidence
estimation methods typically reduce an entire
reasoning process to a single scalar score, ig-
noring how confidence evolves throughout the
generation. As a result, these methods are
often sensitive to superficial factors such as
response length or verbosity, and struggle to
distinguish correct reasoning from confidently
stated errors. We propose to characterize the
stepwise confidence signal using Signal Tem-
poral Logic (STL). Using a discriminative STL
mining procedure, we discover temporal for-
mulas that distinguish confidence signals of
correct and incorrect responses. Our analysis
found that the STL patterns generalize across
tasks, and numeric parameters exhibit sensi-
tivity to individual questions. Based on these
insights, we develop a confidence estimation
approach that informs STL blocks with param-
eter hypernetworks. Experiments on multiple
reasoning tasks show our confidence scores are
more calibrated than the baselines.
1 Introduction
Large Language Models (LLMs) have demon-
strated strong performance on long-form,
multi-step reasoning involved in mathematical
problem solving, scientific explanation, and legal
analysis (Wei et al., 2022; Wang et al., 2022;
Yao et al., 2023). Despite their fluency, LLMs
remain prone to factually incorrect or logically
flawed responses that are expressed with high
apparent certainty (Kadavath et al., 2022b). Recent
work has also explored enforcing formal temporal
constraints during LLM generation to improve
compliance with safety specifications, for example
via logic-based verification techniques (Kamath
*Equal contribution.et al., 2025). Specifically, we estimate a sample-
level probability that a generated response is
correct conditioned on the input, and evaluate its
calibration against empirical correctness.
Most existing approaches to confidence or uncer-
tainty quantification for LLMs reduce the genera-
tion process to a single scalar score. Representative
methods include aggregating token-level probabili-
ties, computing entropy-based statistics, or estimat-
ing confidence through output consistency across
multiple samples (Ott et al., 2018). While effec-
tive in certain settings, these share acommon lim-
itation:they collapse a sequential reasoning pro-
cess into a scalar, discarding information about
how confidence evolves over time. This makes the
confidence depend heavily on how the response is
verbalized (length, verbosity, and stylistic fillers),
since token-level aggregates can be dominated by
high-probability but low-semantic-content tokens.
We posit that confidence in long-form LLM rea-
soning should be viewed not as a static scalar, but as
atemporal signal(Dong et al., 2018; Ribeiro et al.,
2020) that reflects the evolution of model certainty
over reasoning steps. To capture such temporal pat-
terns, we derive stepwise confidence signals from
segmented reasoning responses and characterize
them usingSignal Temporal Logic (STL)(Maler
and Nickovic, 2004), which provides a formal lan-
guage for specifying temporal constraints over real-
valued signals (Linard and Tumova, 2020; Nicoletti
et al., 2024). STL yields a quantitative robustness
score that indicates how well a confidence trajec-
tory satisfies a given temporal specification (Fig-
ure 1). Guided by STL robustness, we use a dis-
criminative STL mining procedure to automatically
discover temporal formulas that best distinguish
correct from incorrect LLM responses.
We analyze the structure and variability of tem-
poral confidence patterns across tasks and in-
stances, and use these insights to guide the design
of our confidence estimation framework. Based
1arXiv:2601.13387v1  [cs.CL]  19 Jan 2026…
……
…Divide 2 teachers and 4 students into 2 groups and arrange them to participate … 
Attention Matrix:  A0
Attention Matrix:  A1
…W1
Instruction: x
Response: y
Step 1: Calculating Attention
For each item rj in Wi ci , calculate the average of non-zero 
elements  as the current step confidence  Final estimated uncertainty : PM=  pn^W0
Normalization 
with Softmax Filtering with  
Heaviside 
step function First, we need to divide 2 teachers and 4 students into 2 groups, each … (step 1) 
Next, we select 2 students out of 4 for the first group. This can be done in  …  (step 2) 
 …
…
Wn
Normalization 
with Softmax 
Filtering with  
Heaviside 
step function 
…
 θStep confidence signal: 
       S = [ s1  , s2  , … ,  sn ]
Finally, …  (step n)  STL φ := □_{[1,t]} ( st ≥ μ ) 
Robustness score: ρ(φ, S, t) ∈ ℝFigure 1: Overview of stepwise confidence modeling with STL. Token-level probabilities are aggregated into a
stepwise confidence signal S, on which STL formulas are evaluated to capture temporal confidence patterns via
robustness scores.
on these insights, we design an interpretable confi-
dence estimation framework that separates tempo-
ral structure from parameter instantiation. We fix
STL structures to preserve interpretability, while
enabling question-level adaptation of their continu-
ous parameters via a hypernetwork (Ha et al., 2016;
Garnelo et al., 2018).
Our contributions are as follows: (1) We
introduce discriminative STL mining for modeling
stepwise confidence signals in long-form LLM
reasoning; (2) We show that effective temporal
confidence structures exhibit consistent patterns
across datasets and task types, suggesting reusable
temporal signatures, particularly for incorrect
reasoning; (3) Within a fixed task setting, we show
that while a single STL structure can generalize
across questions, its optimal parameters can
exhibit significant question-level variability; (4)
We propose an interpretable confidence estimation
framework that combines STL robustness with a
hypernetwork-based parameterization, improving
standard calibration metrics while preserving
temporal structure.
2 Background and Problem
2.1 Probability in LLMs
We consider a family of language tasks indexed
by task type T. Given an input prompt x, a Large
Language Model (LLM) Mgenerates a response
y= [y 1, . . . , y m]in an autoregressive manner. At
each decoding step, the model assigns a probability
to the generated token conditioned on the input and
preceding tokens. We denote by cT(x,y)∈ {0,1}
a binary correctness predicate indicating whether
the generated response yis correct for input xun-
der task type T, as determined by task-specific eval-
uation protocols. The goal of confidence estimation
is to produce a scalar score reflecting the likelihood
that a generated response is correct. A common
baseline is to use the joint generation probabilityP(y|x) as confidence. However, this quantity
suffers from severe length bias, assigning smaller
values to longer responses regardless of correct-
ness (Li et al., 2025b), making it unsuitable for
long-form reasoning.
Problem Formulation.Our objective is to esti-
mate a confidence score ˆP∈[0,1] that approxi-
mates the posterior probability P(cT(x,y) = 1|
x). We assume access to labeled input–output pairs
{(xi,yi, ci)}N
i=1and learn a confidence model S
mapping(x,y)to ˆP=S(x,y).
2.2 Evaluation Metrics
We evaluate confidence estimation quality using
standard calibration metrics, primarilyExpected
Calibration Error (ECE)(Guo et al., 2017) and the
Brier Score (BS)(Glenn et al., 1950), which quan-
tify the alignment between predicted confidence
and empirical correctness. Formal definitions are
provided in the appendix.
2.3 Stepwise Confidence Signal and STL
We represent a generated response yas a sequence
ofnconsecutive segments, where each segment
corresponds to a contiguous subsequence of tokens.
This segmentation induces a stepwise representa-
tion of the generation process and serves as the
temporal axis for subsequent confidence model-
ing. In practice, such a decomposition may follow
linguistic boundaries (e.g., sentences) or semantic
reasoning steps (e.g., those produced by Chain-of-
Thought prompting). Our formulation requires con-
sistent segmentation within each task — but does
not assume any specific method, which is treated
as a design choice.
We define thestepwise confidence signal sjas
the aggregated confidence associated with segment
uj. Specifically, we compute sjas the arithmetic
2mean of the probabilities of its constituent tokens:
sj=1
LjLj−1X
k=0P(ytj+k|y<tj+k,x).(1)
This process transforms the token-level proba-
bility sequence into a coarser-grained confidence
signal s= [s 1, s2, . . . , s n], which serves as the
foundation for logic-based modeling.
STL Syntax.Signal Temporal Logic (STL) pro-
vides a formalism for specifying temporal con-
straints over real-valued signals (Maler and Nick-
ovic, 2004). In this work, we use STL to describe
temporal patterns over the stepwise confidence sig-
nalS= [s 1, . . . , s n].
An STL formula consists of a fixed logical struc-
tureφtogether with continuous parameters θ, such
as predicate thresholds and temporal bounds, and is
denoted by φθ. The basic building block is a pred-
icate µ, typically of the form µ≡s t≥c, where
larger values indicate higher confidence.
STL Robustness.STL formulas are evaluated un-
der quantitative robustness semantics, yielding a
score that summarizes how well a stepwise confi-
dence trajectory satisfies a temporal pattern.
3 Related Work
3.1 Uncertainty Quantification for LLMs
We study response-levelconfidence estimation:
predicting the probability that a specific gener-
ated response is correct, rather than decomposing
aleatoric/epistemic uncertainty. While UQ is well-
studied in MT (Ott et al., 2018; Wang et al., 2021)
and classification (Beck et al., 2016), free-form
LLM generation is harder due to its effectively un-
bounded output space. Existing approaches can
be broadly categorized intofour lines of research:
logit-based, verbalized uncertainty, consistency-
based, and internal-state-based.
Logit-based:Early autoregressive UQ approaches
rely on output probabilities, including cumulative
token-wise likelihoods, predictive entropy, and max
probability (Kadavath et al., 2022b; Jiang et al.,
2021). Length-normalized variants have been pro-
posed to mitigate sentence-length bias (Malinin and
Gales, 2021). However, for long-form reasoning
they conflate semantic and auxiliary tokens, often
causing miscalibration.
Verbalized Uncertainty:Verbalized uncertainty
prompts or fine-tunes LLMs to express confidence
in natural language (Lin et al., 2022a; Tian et al.,2023), including prompt/sampling schemes (Xiong
et al., 2024) and targeted fine-tuning (Amayuelas
et al., 2024); these often require additional training
or domain adaptation (Kadavath et al., 2022b; Man-
akul et al., 2023). Although promising, these meth-
ods often require additional training (e.g., RLHF or
fine-tuning) or domain-specific adaptation to per-
form well (Kadavath et al., 2022b; Manakul et al.,
2023).
Consistency-based:To address the intractable rea-
soning space and semantic equivalence, a promi-
nent line of work estimates uncertainty from mul-
tiple sampled outputs in a semantic space.Self-
Consistency(Wang et al., 2023) estimates con-
fidence from agreement among diverse genera-
tions, whileSemantic Entropy(Kuhn et al., 2023)
formalizes this idea via semantic clustering and
entropy-based scoring. Extensions use entailment-
based black-box clustering (Farquhar et al., 2024),
self-reflection (Kirchhof et al., 2025), paraphrased
prompting (Li et al., 2025a), and clarification se-
quences (Hou et al., 2024). Despite their effec-
tiveness, these methods typically require multiple
model runs and often rely on additional compo-
nents such as external encoder models, introducing
non-trivial inference overhead.
Internals-based:Internals-based methods esti-
mate uncertainty from hidden states or token at-
tribution, e.g., SAR (Duan et al., 2024); related ap-
proaches exploit model internals (Beigi et al., 2024;
Azaria and Mitchell, 2023) but can be expensive
(e.g., factorial SAR) or require large in-domain
validation sets. Despite progress, LLMs remain
overconfident in zero-shot and out-of-domain set-
tings (Liu et al., 2025; Desai and Durrett, 2020),
and structured reasoning remains comparatively
under-explored.
3.2 Temporal Logics
Temporal logic, originally developed for formal
verification (Pnueli, 1977), provides a principled
framework for specifying temporal patterns over
signals, andSignal Temporal Logic(STL) (Maler
and Nickovic, 2004) extends it to real-valued sig-
nals with quantitative robustness semantics. In this
work, we apply STL to discrete-time confidence
trajectories derived from stepwise LLM reason-
ing, using robustness to summarize temporal confi-
dence patterns. The quantitative nature of STL has
enabled its integration into neuro-symbolic learn-
ing (Garcez et al., 2019), including differentiable
constraint enforcement in robotics and time-series
3
0
x-amz-checksum-crc32:SulO7A==


------------------------------------------------------------------------------------------

## 🎯 GraphSB_Boosting_Imbalanced_Node_Classification_on_Graphs_through_Structural_Balance
**Категория:** None | **Дата:** 2026-01-27
**ТЕМА:** ['Machine Learning']
## Ссылка на источник https://arxiv.org/abs/2601.19352

### Саммари статьи
3c68
GraphSB: Boosting Imbalanced Node Classification on Graphs through
Structural Balance
Zhixiao Wang1,Chaofan Zhu1,Qihan Feng1,Jian Zhang1,Xiaobin Rui1∗,Philip Yu2
1China University of Mining and Technology
2University of Illinois Chicago
zhxwang@cumt.edu.cn, chaofanz@cumt.edu.cn, fengqh@cumt.edu.cn,
zhangjian10231209@cumt.edu.cn, ruixiaobin@cumt.edu.cn, psyu@uic.edu
Abstract
Imbalanced node classification is a critical chal-
lenge in graph learning, where most existing
methods typically utilize Graph Neural Networks
(GNNs) to learn node representations. These meth-
ods can be broadly categorized into the data-level
and the algorithm-level. The former aims to synthe-
size minority-class nodes to mitigate quantity im-
balance, while the latter tries to optimize the learn-
ing process to highlight minority classes. How-
ever, neither of them addresses the inherently im-
balanced graph structure, which is a fundamen-
tal factor that incurs majority-class dominance and
minority-class assimilation in GNNs. Our theoret-
ical analysis further supports this critical insight.
Therefore, we propose GraphSB (Graph Struc-
tural Balance), a novel framework that incorporates
Structural Balanceas a key strategy to address
the underlying imbalanced graph structure before
node synthesis.Structural Balanceperforms
a two-stage structure optimization:Structure
Enhancementthat mines hard samples near de-
cision boundaries through dual-view analysis and
enhances connectivity for minority classes through
adaptive augmentation, andRelation Diffusion
that propagates the enhanced minority context
while simultaneously capturing higher-order struc-
tural dependencies. Thus, GraphSB balances struc-
tural distribution before node synthesis, enabling
more effective learning in GNNs. Extensive ex-
periments demonstrate that GraphSB significantly
outperforms the state-of-the-art methods. More im-
portantly, the proposedStructural Balancecan be
seamlessly integrated into state-of-the-art methods
as a simple plug-and-play module, increasing their
accuracy by an average of4.57%.
1 Introduction
In recent years, Graph Neural Networks (GNNs) [Wuet al.,
2021 ]have become a powerful tool for graph representation
∗Corresponding author.learning, achieving advanced performance in various graph-
related downstream tasks [Yuet al., 2023; Wanget al., 2023;
Salha-Galvanet al., 2022 ]. Among them, node classifica-
tion[Kipf and Welling, 2016; Xiaoet al., 2022 ]is one of the
most fundamental tasks. The goal of node classification is to
predict the labels of the unlabeled nodes based on the charac-
teristics of the nodes.
In fact, real-world graphs often exhibit a significant class
imbalance, with the number of samples varying greatly across
different classes [Parket al., 2021; Liuet al., 2023 ]. Thus, the
classification of unbalanced nodes has become a critical chal-
lenge. To address this problem, researchers have explored
various GNN-based approaches, which can be broadly cat-
egorized into two types: data-level methods and algorithm-
level methods. Data-level methods aim to balance the train-
ing process by synthesizing additional samples for the minor-
ity class. To highlight minority-class nodes, algorithm-level
methods attempt to optimize the learning process. Despite
their contributions, they are essentially palliative as they fail
to directly tackle the underlying imbalanced structure.
In practice, the imbalanced graph structure usually exhibits
strong asymmetry: minority-class nodes tend to have sparser
neighborhoods. This induces biased neighbor aggregation
during message passing, severely degrading the representa-
tion quality of minority-class nodes. Therefore, failing to
address fundamental structural imbalance not only limits the
benefits of quantity balancing techniques, but also perpetu-
ates structural bias throughout the learning process, resulting
in a final biased representation.
Figure 1 illustrates how structural imbalance hinders the
learning of minority classes. Due to the scarcity of edges
connecting minority-class nodes, these nodes receive insuf-
ficient same-class context during neighborhood aggregation.
Consequently, their latent representations are progressively
compressed toward the majority-class subspace. This com-
pression effect is particularly harmful to thehard samples
located near decision boundaries,i.e., nodes that naturally lie
between class regions and rely on stronger same-class sup-
port to maintain correct classification. Furthermore, as GNN
depth increases, minority-class context decays exponentially,
while majority-class context accumulates through repeated
aggregation, further shrinking decision boundaries. This phe-
nomenon arises fundamentally from the intrinsic coupling be-
tween node embeddings and graph structure, highlighting thearXiv:2601.19352v1  [cs.LG]  27 Jan 2026need for structural intervention at early stages.
GNN Depth 
Majority Sample
Minority Sample
Minority Node Embedding
 Hard Sample First Neighbor Agg
Real Edge Second Neighbor AggDecision BoundaryDecision Boundar y
Decision Boundar yshrink
Figure 1: Imbalanced node learning on graphs
Motivated by these observations, we propose a compre-
hensive imbalanced node classification framework named
GraphSB(Graph Structural Balance) to address the inherent
imbalanced graph structure. Our contributions are summa-
rized as follows:
•Theoretical Analysis. Our theoretical analysis re-
veals that the imbalanced graph structure leads GNNs
to suffer from majority-class dominance and minority-
class assimilation, causing biased representation of the
minority-class nodes. Such bias will accumulate in sub-
sequent node synthesis and learning stages, resulting in
significantly degraded representation quality.
•Model Design. We propose GraphSB, which incor-
poratesStructural Balanceas a key strategy to ad-
dress imbalanced graph structure.Structural Bal-
anceutilizes a two-stage optimization: (1)Structure
Enhancementmines hard samples near decision
boundaries via dual-view analysis and strengthens
their minority-class connectivity, while (2)Relation
Diffusionpropagates the enhanced minority context
through multi-step iterative diffusion while capturing
higher-order structural dependencies.
•Experimental Evaluation. Experiments on eight
datasets demonstrate that GraphSB significantly outper-
forms the state-of-the-art methods. More importantly,
Structural Balancecan be seamlessly integrated into
state-of-the-art methods as a simple plug-and-play mod-
ule, increasing their accuracy by4.57%on average.
2 Related Work
2.1 Data-level Methods
Data-level methods are dedicated to alleviating this quantity
imbalance across different classes [Dinget al., 2022 ]. For
example, GraphENS [Parket al., 2021 ]synthesizes minority-
class nodes by incorporating information from majority
classes. GraphSMOTE [Zhaoet al., 2024 ]extends the idea
of SMOTE [Chawlaet al., 2002 ]to generate synthetic nodes.
GraphMixup [Wuet al., 2022 ]enhances Mixup [Zhanget
al., 2017 ]to synthesize minority-class nodes in the embed-
ding space and subsequently determines the neighborhoodsof the newly generated nodes with a pre-trained edge predic-
tor. GraphSHA [Liet al., 2023 ]aims to synthesize harder
minority samples. Although these methods increase the num-
ber of minority-class samples, they focus mainly on quantity
rebalancing while overlooking the inherent structural imbal-
ance of graphs, fundamentally limiting their effectiveness.
2.2 Algorithm-level Methods
Algorithm-level methods mitigate class imbalance by opti-
mizing the learning process through penalty functions, model
regularization, or other sophisticated techniques. For exam-
ple, DR-GCN [Shiet al., 2020 ]tackles the class-imbalanced
problem by imposing two types of regularization, combin-
ing conditional adversarial training with latent distribution
alignment. ReNode [Chenet al., 2021 ]incorporates modified
loss functions or designed penalty terms to emphasize minor-
ity classes. TAM [Songet al., 2022 ]adjusts classification
margins using local neighbor distributions but ignores global
structural dominance. BAT [Liuet al., 2024 ]augments the
weights of minority-class nodes to alleviate ambivalent and
long-range message passing, while IceBerg [Liet al., 2025 ]
leverages debiased self-training and decoupled propagation
to reach distant unlabeled nodes. Despite these efforts, such
methods primarily optimize the learning process to obtain a
better embedding space for minority-class nodes, yet fail to
address the fundamental imbalanced graph structure.
3 Structural Imbalance in GNNs
3.1 Preliminaries
LetG= (V, E, X)denote an attributed graph with node
setV, edge setE, and feature matrixX∈Rn×p 0, where
n=|V|andx i∈Rp0represents the feature vector of
nodei∈ {1,···, n}. In message passing neural networks
(MPNN) [Gilmeret al., 2017 ], the node representationh(ℓ)
i∈
Rpℓat layerℓ≥0evolves through message passing. Let
N(i)denote nodei’s neighborhood,ϕ ℓ,ψℓrefer to the learn-
able update function and the message aggregation function,
respectively. The layer-wise propagation rule is given by:
h(ℓ+1)
i =ϕℓ
h(ℓ)
i,X
j∈N(i)ˆAijψℓ(h(ℓ)
i, h(ℓ)
j)
,(1)
where ˆA= ˜D−1/2˜A˜D−1/2refers to the normalized adja-
cency matrix with self-loops. ˜A=A+Idenotes the adja-
cency matrix with self-connections and ˜Drepresents the de-
gree matrix of ˜A.
Consider a class-imbalanced graphGwith two classes,i.e.,
V=V mi∪V ma, whereV miandV marefer to the nodes of the
minority and majority class, respectively. Letn 1=|V ma|and
n2=|V mi|, the imbalance ratio isβ=n 1/n2≫1. Under a
Stochastic Block Model [Hollandet al., 1983 ]SBM(n, p, q)
with intra-class edge probabilitypand inter-class edge prob-
abilityq, wherep≫q, the expected degrees for a minority-
class node and a majority-class node are:
• minority-class:E[d mi] =n 1p+n 2q=n 1(p+qβ)
• majority-class:E[d ma] =n 1q+n 2p=n 1(q+pβ)Letτ=E[d ma]
E[d mi]=q+pβ
p+qβ>1denote the degree dispar-
ity that quantifies the structural advantage of majority-class
nodes, we then analyze how the structural imbalance incurs
majority-class dominance and minority-class assimilation.
3.2 Majority-class Dominance
In class imbalance, two key mechanisms disadvantage
minority-class nodes: information dilution through over-
squashing and gradient dominance from the majority class.
The over-squashing phenomenonin GNNs, first ana-
lyzed by Alonet al. [Alon and Yahav, 2020 ], manifests as
exponential decay of information from distant nodes. Con-
sider an MPNN with node featuresX∈Rn×p 0, learnable
update functionϕ ℓ, and message aggregation functionψ ℓ,
satisfying∥∇ϕ ℓ∥ ≤η 1,∥∇ψ ℓ∥ ≤η 2whereη 1andη 2are
two constants. For nodesu, v∈Vwith geodesic distance
dG(u, v) =r+ 1, the Jacobian sensitivity is as follows:
∂h(r+1)
u
∂xv≤(η 1η2)r+1(ˆAr+1)uv.(2)
In bottleneck structures such asb-ary trees,( ˆAr+1)uv=
O(b−r)confirms exponential information distortion.
Definition 1.Information Dilutionis the phenomenon
where information propagation between nodes decays rapidly
as the distanceLincreases. In class-imbalanced graphs, this
decay is further accelerated by the degree disparityτ, with
the attenuation factor amplified byτ−L.
The relationship between information dilution and the
over-squashing phenomenon is formally quantified in the fol-
lowing Theorem 1 (detailed proof given in Appendix A).
Theorem 1.For a path of lengthLin a class-imbalanced
graph with imbalance ratioβand degree disparityτ, the ex-
pected path weight satisfies:
E[W L] =W(L)
E[d mi]Lβ+τ
τ(β+ 1)L
,(3)
whereW Ldenotes the weight of information propagated
along a path of lengthL.W(L)=QL
ℓ=1∇ϕℓ∇ψℓrepresents
the cumulative weight matrix product.
Theorem 1 demonstrates that the majority-class nodes ac-
celerate information dilution, causing node features of the
minority-class to be overwhelmed in message passing.
To better characterize the mechanism where the majority-
class nodes dominate the learning process, we formally intro-
duce the concept of Gradient Dominance as follows.
Definition 2.Gradient Dominanceis the phenomenon in
class-imbalanced graphs where the gradients associated with
majority-class nodes overwhelmingly influence the parame-
ter updates during training, thereby suppressing the learning
information from minority-class nodes.
The effect of Gradient Dominance is formally quantified in
Theorem 2 (detailed proof given in Appendix A).Theorem 2.Assume path weightW Lis independent of node
degrees, then the expected ratio of gradient magnitudes be-
tween a minority-class nodeu∈V miand a majority-class
nodev∈V maat depthLis:
E[∥∇ θL(u)∥]
E[∥∇ θL(v)∥]∝1
β.(4)
Theorem 2 demonstrates that the numerical advantage and
higher degree of majority-class nodes make them dominant
in information propagation and gradient updates.
3.3 Minority-class Assimilation
Definition 3.Minority-class Assimilationrefers to the pro-
gressive convergence of minority-class nodes’ representa-
tions toward the centroids of majority classes during GNN
message passing. As a result, the distinctive characteristics
of the minority-class nodes are overwhelmed by the predomi-
nant influence of the majority-class nodes, leading to reduced
class separability in the deeper layers.
The following Theorem 3 reveals how the node features of
minority classes assimilate to those of the majority classes as
the network depth increases.
Theorem 3.In class-imbalanced graphs with imbalance ra-
tioβ≫1, the centroid distance∆(ℓ)=µ(ℓ)
mi−µ(ℓ)
mabetween
minority and majority classes decays exponentially with net-
work depth, which holds:
∥∆(ℓ)∥ ≤C·(σ max(W)λ 2(M))ℓ∥∆(0)∥,(5)
whereCis a constant,∆(0)refers to the initial centroid dis-
tance,σ max(W)represents the maximum singular value of
weight matrixW, andλ 2(M)denotes the second eigenvalue
of the propagation matrixMwithσ max(W)λ 2(M)<1.
For example, letp= 0.5,q= 0.1, andβ= 10, we have
M=
0.333 0.196
0.067 0.980
, λ 2≈0.313.
Sinceλ 2<1, it governs the convergence rate. By spectral
decomposition, the magnitude of∆(ℓ)decays asO(0.313ℓ),
showing how rapidly the node features of minority classes
assimilate with the increase of network depth.
4 Methodology
Building on the theoretical analysis presented in the above
section, we propose GraphSB (Graph Structural Balance)
to explicitly address the inherent structural imbalance in
graphs. As illustrated in Figure 2,Structural Bal-
anceinvolves a two-stage optimization: (1)Structure
Enhancementmines hard samples and enhances minor-
ity connectivity through adaptive augmentation, and (2)
Relation Diffusionpropagates the enhanced minority con-
text while capturing higher-order structural dependencies.
For the quantity balance, we follow the same approach as
GraphMixup [Wuet al., 2022 ]. It is worth noting that our pro-
posedStructural Balancecan be integrated into any state-
of-the-art methods besides GraphMixup (Complexity analy-
sis is detailed in the Appendix C).
0
x-amz-checksum-crc32:hVoQtg==


------------------------------------------------------------------------------------------