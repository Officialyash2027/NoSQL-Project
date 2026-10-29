# Reference verification

Checked on 2026-10-18 against arXiv abstract pages and the Crossref metadata API
(`https://api.crossref.org/works/<DOI>`). Publisher pages (Springer, Wiley, ScienceDirect) blocked
automated access, so Crossref was used as the authoritative source for DOIs.

| # in final paper | Reference | Status | Notes / corrections |
|---|---|---|---|
| [1] | S. Ferreira, J. Mendonça, B. Nogueira, W. Tiengo, E. Andrade, "Impacts of data consistency levels in cloud-based NoSQL for data-intensive applications," *J. Cloud Computing*, 13(1), art. 158, 2024. DOI 10.1186/s13677-024-00716-7 | **Verified** (Crossref) | Matches Review II. |
| [2] | S. Ferreira, J. Mendonça, E. Andrade, "Experimental performance analysis of data consistency levels in NoSQL databases," *Software: Practice and Experience*, 55(6), 1059–1070, 2025. DOI 10.1002/spe.3412 | **Verified** (Crossref) | Review II wrote "S. Ferreira, E. Andrade et al."; full author list and volume/pages added. |
| [3] | A. Maamari, M. Nasar, "Enhancing database security in NoSQL systems using machine learning-based anomaly detection," 2025 | **NOT VERIFIED** | No matching record found by web search. Venue, volume and DOI unknown. **The students must confirm the source (where they read it) or replace the citation before submission.** It is cited in the paper with a "[venue to be confirmed]" marker. |
| [4] | S. Solat, "Sharding distributed databases: A critical review," arXiv:2404.04384, 2024 | **Verified** (arXiv) | Earlier reports listed no author; author added. |
| [5] | D. Bodra, S. Khairnar, "Comparative performance analysis of modern NoSQL data technologies: Redis, Aerospike, and Dragonfly," arXiv:2510.08863, 2025; also *J. Res. Innov. Technol.*, 4(2), 193–200, 2025 | **Verified** (arXiv) | Review II misspelled the first author as "Borda"; Review I's "Bodra" is correct. |
| [6] | M. Abbasi, P. Váz, J. Silva, F. Cardoso, F. Sá, P. Martins, "Multi-database NoSQL performance analysis: Empirical selection framework for cloud-native applications," *Procedia Computer Science*, vol. 280, pp. 745–752, 2026. DOI 10.1016/j.procs.2026.04.094 | **Verified** (Crossref) | Volume, pages and DOI added. |
| [7] | B. F. Cooper et al., "Benchmarking cloud serving systems with YCSB," SoCC 2010, pp. 143–154. DOI 10.1145/1807128.1807152 | **Verified** | — |
| [8] | D. Karger et al., "Consistent hashing and random trees…," STOC 1997, pp. 654–663. DOI 10.1145/258533.258660 | **Verified** | — |
| [9] | F. T. Liu, K. M. Ting, Z.-H. Zhou, "Isolation forest," ICDM 2008, pp. 413–422. DOI 10.1109/ICDM.2008.17 | **Verified** | — |
| [10] | F. Pedregosa et al., "Scikit-learn: Machine learning in Python," *JMLR*, 12, 2825–2830, 2011 | Standard citation (JMLR has no DOI) | — |
| [11] | E. Brewer, "CAP twelve years later: How the 'rules' have changed," *Computer*, 45(2), 23–29, 2012. DOI 10.1109/MC.2012.37 | **Verified** | — |
| [12] | P. Bailis et al., "Probabilistically bounded staleness for practical partial quorums," *PVLDB*, 5(8), 776–787, 2012. DOI 10.14778/2212351.2212359 | **Verified** — new | Basis of the staleness model in E4. |
| [13] | G. DeCandia et al., "Dynamo: Amazon's highly available key-value store," *ACM SIGOPS OSR*, 41(6), 205–220, 2007. DOI 10.1145/1323293.1294281 | **Verified** — new | Quorums + virtual nodes. |
| [14] | A. Lakshman, P. Malik, "Cassandra: A decentralized structured storage system," *ACM SIGOPS OSR*, 44(2), 35–40, 2010. DOI 10.1145/1773912.1773922 | **Verified** — new | Tunable consistency. |
| [15] | A. Ron, A. Shulman-Peleg, A. Puzanov, "Analysis and mitigation of NoSQL injections," *IEEE Security & Privacy*, 14(2), 30–39, 2016. DOI 10.1109/MSP.2016.36 | **Verified** — new | Motivates the operator-rejection rule and injection feature. |

Review I references not carried into the final paper (no authors/venue details could be confirmed
quickly, and the implementation does not depend on them): the IEEE retail/e-commerce comparison
(2024/2025), the IAEME IJCC 2024 article, the IJFMR 2024 article, the WJAETS 2025 article and the
MIPRO 2018 relational-vs-NoSQL comparison. If the students want them in the final paper, they should
add complete, checked entries.
