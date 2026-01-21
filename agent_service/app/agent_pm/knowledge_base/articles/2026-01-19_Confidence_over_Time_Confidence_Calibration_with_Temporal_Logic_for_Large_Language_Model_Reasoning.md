3578
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


0
x-amz-checksum-crc32:kyIcww==

