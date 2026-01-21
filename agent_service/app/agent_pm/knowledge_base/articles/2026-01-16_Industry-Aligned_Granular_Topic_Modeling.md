3111
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
# low-level topics 77 114 279
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


0
x-amz-checksum-crc32:1wyuPg==

