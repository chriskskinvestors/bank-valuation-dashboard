"""Compact, cell-faithful copies of four real OTC earnings releases
(tables rebuilt from the page's own cell text — tags/styles dropped,
row/cell structure kept — plus the prose the prose extractors read).
Hand-read values live in tests/test_otc_eps_mcap.py. Fetched 2026-10-06.
"""

# https://investors.freedom.bank/2026-07-31-Freedom-Financial-Holdings-Announces-Earnings-for-Second-Quarter-of-2026
FDVA_2Q26 = '''
<html><body>
<p>Freedom Financial Holdings (OTCQX: FDVA), the holding company for The Freedom Bank of Virginia today announced net income of $289,621 or $0.04 per diluted share for the second quarter compared to net income of $1,160,338, or $0.16 per diluted share for the three months ended March 31, 2026, and net income of $799,896 or $0.11 per diluted share for the three months ended June 30, 2025.</p>
<p>Tangible Book Value per share improved during the quarter by $0.12 to $12.20 on June 30, 2026, compared to $12.08 on March 31, 2026.</p>
<p>The tangible book value of the Company&#x27;s common stock on June 30, 2026, was $12.20 per share compared to $12.08 on March 31, 2026.</p>
<table>
<tr><td>FREEDOM FINANCIAL HOLDINGS</td></tr>
<tr><td>CONSOLIDATED BALANCE SHEETS</td></tr>
<tr><td></td><td>(Unaudited)</td><td></td><td>(Unaudited)</td><td></td><td>(Audited)</td></tr>
<tr><td></td><td>June 30,</td><td></td><td>March 31,</td><td></td><td>December 31,</td></tr>
<tr><td></td><td>2026</td><td></td><td>2026</td><td></td><td>2025</td></tr>
<tr><td>ASSETS</td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Cash and Due from Banks</td><td>$ 5,458,898</td><td></td><td>$ 4,527,248</td><td></td><td>$ 4,540,452</td></tr>
<tr><td>Interest Bearing Deposits with Banks</td><td>26,936,559</td><td></td><td>33,646,083</td><td></td><td>70,078,398</td></tr>
<tr><td>Securities Available-for-Sale</td><td>150,739,160</td><td></td><td>156,852,319</td><td></td><td>158,446,651</td></tr>
<tr><td>Securities Held-to-Maturity</td><td>17,846,586</td><td></td><td>18,242,410</td><td></td><td>19,242,952</td></tr>
<tr><td>Restricted Stock Investments</td><td>5,655,600</td><td></td><td>4,468,100</td><td></td><td>5,435,300</td></tr>
<tr><td>Loans Held for Sale</td><td>13,812,357</td><td></td><td>12,077,102</td><td></td><td>4,283,305</td></tr>
<tr><td>PPP Loans Held for Investment</td><td>112,661</td><td></td><td>112,661</td><td></td><td>117,738</td></tr>
<tr><td>Other Loans Held for Investment</td><td>763,549,261</td><td></td><td>770,827,073</td><td></td><td>762,435,469</td></tr>
<tr><td>Allowance for Loan Losses</td><td>(8,058,550)</td><td></td><td>(7,696,395)</td><td></td><td>(13,897,689)</td></tr>
<tr><td>Net Loans</td><td>769,415,729</td><td></td><td>775,320,441</td><td></td><td>752,938,823</td></tr>
<tr><td>Bank Premises and Equipment, net</td><td>1,499,670</td><td></td><td>1,189,003</td><td></td><td>728,030</td></tr>
<tr><td>Accrued Interest Receivable</td><td>4,525,299</td><td></td><td>4,463,908</td><td></td><td>4,059,501</td></tr>
<tr><td>Deferred Tax Asset</td><td>7,542,341</td><td></td><td>7,579,833</td><td></td><td>7,428,794</td></tr>
<tr><td>Bank-Owned Life Insurance</td><td>28,936,144</td><td></td><td>28,700,809</td><td></td><td>28,469,911</td></tr>
<tr><td>Right of Use Asset, net</td><td>5,339,622</td><td></td><td>5,657,815</td><td></td><td>1,582,514</td></tr>
<tr><td>Other Assets</td><td>14,970,136</td><td></td><td>12,178,246</td><td></td><td>12,931,701</td></tr>
<tr><td>Total Assets</td><td>$ 1,038,865,744</td><td></td><td>$1,052,826,215</td><td></td><td>$ 1,065,883,027</td></tr>
<tr><td>LIABILITIES AND STOCKHOLDERS&#x27; EQUITY</td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Deposits</td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Demand Deposits</td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Non-interest Bearing</td><td>$ 142,064,271</td><td></td><td>$ 149,338,747</td><td></td><td>$ 149,516,366</td></tr>
<tr><td>Interest Bearing</td><td>540,859,808</td><td></td><td>548,420,087</td><td></td><td>555,799,698</td></tr>
<tr><td>Savings Deposits</td><td>2,151,753</td><td></td><td>2,289,866</td><td></td><td>1,989,696</td></tr>
<tr><td>Time Deposits</td><td>189,748,053</td><td></td><td>217,315,240</td><td></td><td>206,958,024</td></tr>
<tr><td>Total Deposits</td><td>874,823,885</td><td></td><td>917,363,940</td><td></td><td>914,263,784</td></tr>
<tr><td>Federal Home Loan Bank Advances</td><td>45,000,000</td><td></td><td>20,000,000</td><td></td><td>40,000,000</td></tr>
<tr><td>Other Borrowings</td><td>-</td><td></td><td>112,661</td><td></td><td>117,737</td></tr>
<tr><td>Subordinated Debt (Net of Issuance Costs)</td><td>19,967,531</td><td></td><td>19,948,049</td><td></td><td>19,928,568</td></tr>
<tr><td>Accrued Interest Payable</td><td>546,253</td><td></td><td>887,034</td><td></td><td>913,813</td></tr>
<tr><td>Lease Liability</td><td>5,697,751</td><td></td><td>5,878,842</td><td></td><td>1,666,836</td></tr>
<tr><td>Other Liabilities</td><td>7,682,523</td><td></td><td>4,385,636</td><td></td><td>4,852,310</td></tr>
<tr><td>Total Liabilities</td><td>$ 953,717,943</td><td></td><td>$ 968,576,162</td><td></td><td>$ 981,743,048</td></tr>
<tr><td>Stockholders&#x27; Equity</td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Preferred stock, $0.01 par value, 5,000,000 shares authorized:</td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>0 Shares Issued and Outstanding, June 30, 2026, March 31, 2026 and December 31, 2025</td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Common Stock, $0.01 Par Value, 25,000,000 Shares authorized:</td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>23,000,000 Shares Voting and 2,000,000 Shares Non-voting.</td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Voting Common Stock:</td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>6,978,754 , 6,973,747 and 6,984,013 Shares Issued and Outstanding</td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>at June 30, 2026, March 31, 2026 and December 31, 2025 respectively</td><td>69,788</td><td></td><td>69,737</td><td></td><td>69,840</td></tr>
<tr><td>Non-Voting Common Stock:</td><td>-</td><td></td><td>-</td><td></td><td>-</td></tr>
<tr><td>0 Shares Issued and Outstanding at June 30, 2026, March 31, 2026</td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>and December 31, 2025 respectively)</td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Additional Paid-in Capital</td><td>56,565,519</td><td></td><td>56,029,673</td><td></td><td>56,624,236</td></tr>
<tr><td>Accumulated Other Comprehensive Income, Net</td><td>(14,573,309)</td><td></td><td>(14,645,539)</td><td></td><td>(14,189,941)</td></tr>
<tr><td>Retained Earnings</td><td>43,085,803</td><td></td><td>42,796,182</td><td></td><td>41,635,844</td></tr>
<tr><td>Total Stockholders&#x27; Equity</td><td>$ 85,147,801</td><td></td><td>$ 84,250,053</td><td></td><td>$ 84,139,979</td></tr>
<tr><td>Total Liabilities and Stockholders&#x27; Equity</td><td>$ 1,038,865,744</td><td></td><td>$1,052,826,215</td><td></td><td>$ 1,065,883,027</td></tr>
</table>
<table>
<tr><td>FREEDOM FINANCIAL HOLDINGS</td></tr>
<tr><td>CONSOLIDATED STATEMENTS OF OPERATIONS</td></tr>
<tr><td></td><td></td><td>(Unaudited)</td><td>(Unaudited)</td><td></td><td>(Unaudited)</td><td>(Unaudited)</td></tr>
<tr><td></td><td></td><td>For the three</td><td>For the three</td><td></td><td>For the six</td><td>For the six</td></tr>
<tr><td></td><td></td><td>months ended</td><td>months ended</td><td></td><td>months ended</td><td>months ended</td></tr>
<tr><td></td><td></td><td>June 30, 2026</td><td>June 30, 2025</td><td></td><td>June 30, 2026</td><td>June 30, 2025</td></tr>
<tr><td>Interest Income</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Interest and Fees on Loans</td><td></td><td>$ 11,650,836</td><td>$ 11,673,927</td><td></td><td>$ 22,927,087</td><td>$ 24,377,509</td></tr>
<tr><td>Interest on Investment Securities</td><td></td><td>1,787,268</td><td>2,450,914</td><td></td><td>3,560,347</td><td>5,064,172</td></tr>
<tr><td>Interest on Deposits with Other Banks</td><td></td><td>285,510</td><td>750,611</td><td></td><td>988,900</td><td>1,013,118</td></tr>
<tr><td>Total Interest Income</td><td></td><td>13,723,614</td><td>14,875,452</td><td></td><td>27,476,334</td><td>30,454,799</td></tr>
<tr><td>Interest Expense</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Interest on Deposits</td><td></td><td>6,151,712</td><td>7,275,073</td><td></td><td>12,491,753</td><td>14,221,266</td></tr>
<tr><td>Interest on Borrowings</td><td></td><td>592,778</td><td>724,216</td><td></td><td>1,110,069</td><td>1,637,370</td></tr>
<tr><td>Total Interest Expense</td><td></td><td>6,744,490</td><td>7,999,289</td><td></td><td>13,601,822</td><td>15,858,637</td></tr>
<tr><td>Net Interest Income</td><td></td><td>6,979,124</td><td>6,876,162</td><td></td><td>13,874,512</td><td>14,596,162</td></tr>
<tr><td>Provision/(Recovery) for Loan Losses</td><td></td><td>538,805</td><td>688,865</td><td></td><td>598,141</td><td>973,548</td></tr>
<tr><td>Net Interest Income After</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Provision for Loan Losses</td><td></td><td>6,440,319</td><td>6,187,298</td><td></td><td>13,276,371</td><td>13,622,614</td></tr>
<tr><td>Non-Interest Income</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Mortgage Loan Gain-on-Sale and Fee Revenue</td><td></td><td>1,079,890</td><td>797,759</td><td></td><td>2,022,147</td><td>1,455,072</td></tr>
<tr><td>SBA Gain-on-Sale Revenue</td><td></td><td>-</td><td>-</td><td></td><td>-</td><td>-</td></tr>
<tr><td>Service Charges and Other Income</td><td></td><td>327,093</td><td>270,230</td><td></td><td>547,834</td><td>344,121</td></tr>
<tr><td>Servicing Income</td><td></td><td>16,001</td><td>21,045</td><td></td><td>33,494</td><td>47,147</td></tr>
<tr><td>Increase in Cash Surrender Value of Bank-</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>owned Life Insurance</td><td></td><td>235,334</td><td>223,061</td><td></td><td>466,233</td><td>443,925</td></tr>
<tr><td>Total Non-interest Income</td><td></td><td>1,658,318</td><td>1,312,094</td><td></td><td>3,069,708</td><td>2,290,265</td></tr>
<tr><td>Total Revenue</td><td></td><td>8,637,442</td><td>8,188,257</td><td></td><td>16,944,220</td><td>16,886,427</td></tr>
<tr><td>Non-Interest Expenses</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Officer and Employee Compensation</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>and Benefits</td><td></td><td>4,373,967</td><td>3,752,761</td><td></td><td>8,777,587</td><td>7,522,296</td></tr>
<tr><td>Occupancy Expense</td><td></td><td>375,936</td><td>244,279</td><td></td><td>740,877</td><td>486,442</td></tr>
<tr><td>Equipment and Depreciation Expense</td><td></td><td>11,336</td><td>16,619</td><td></td><td>22,048</td><td>25,345</td></tr>
<tr><td>Insurance Expense</td><td></td><td>245,402</td><td>220,346</td><td></td><td>452,001</td><td>446,112</td></tr>
<tr><td>Professional Fees</td><td></td><td>439,501</td><td>559,904</td><td></td><td>785,807</td><td>1,030,213</td></tr>
<tr><td>Data and Item Processing</td><td></td><td>587,093</td><td>595,492</td><td></td><td>1,118,056</td><td>1,133,705</td></tr>
<tr><td>Advertising</td><td></td><td>109,791</td><td>151,676</td><td></td><td>191,391</td><td>234,791</td></tr>
<tr><td>Franchise Taxes and State Assessment Fees</td><td></td><td>329,846</td><td>314,444</td><td></td><td>656,415</td><td>628,658</td></tr>
<tr><td>Mortgage Fees and Settlements</td><td></td><td>153,051</td><td>99,819</td><td></td><td>227,890</td><td>174,548</td></tr>
<tr><td>Other Operating Expense</td><td></td><td>1,119,074</td><td>396,213</td><td></td><td>1,574,469</td><td>690,447</td></tr>
<tr><td>Total Non-interest Expenses</td><td></td><td>7,744,997</td><td>6,351,552</td><td></td><td>14,546,541</td><td>12,372,557</td></tr>
<tr><td>Income Before Income Taxes</td><td></td><td>353,640</td><td>1,147,840</td><td></td><td>1,799,538</td><td>3,540,322</td></tr>
<tr><td>Income Tax Expense/(Benefit)</td><td></td><td>64,019</td><td>347,943</td><td></td><td>349,579</td><td>721,082</td></tr>
<tr><td>Net Income</td><td></td><td>$ 289,621</td><td>$ 799,896</td><td></td><td>$ 1,449,959</td><td>$ 2,819,240</td></tr>
<tr><td>Earnings per Common Share - Basic</td><td></td><td>$ 0.04</td><td>$ 0.11</td><td></td><td>$ 0.20</td><td>$ 0.39</td></tr>
<tr><td>Earnings per Common Share - Diluted</td><td></td><td>$ 0.04</td><td>$ 0.11</td><td></td><td>$ 0.20</td><td>$ 0.39</td></tr>
<tr><td>Weighted-Average Common Shares</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Outstanding - Basic</td><td></td><td>7,098,594</td><td>7,137,779</td><td></td><td>7,101,643</td><td>7,151,171</td></tr>
<tr><td>Weighted-Average Common Shares</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Outstanding - Diluted</td><td></td><td>7,124,543</td><td>7,140,491</td><td></td><td>7,175,023</td><td>7,153,655</td></tr>
</table>
<table>
<tr><td>FREEDOM FINANCIAL HOLDINGS</td></tr>
<tr><td>CONSOLIDATED STATEMENTS OF OPERATIONS</td></tr>
<tr><td></td><td>(Unaudited)</td><td></td><td>(Unaudited)</td><td></td><td>(Unaudited)</td><td></td><td>(Unaudited)</td><td></td><td>(Unaudited)</td></tr>
<tr><td></td><td>For the three</td><td></td><td>For the three</td><td></td><td>For the three</td><td></td><td>For the three</td><td></td><td>For the three</td></tr>
<tr><td></td><td>months ended</td><td></td><td>months ended</td><td></td><td>months ended</td><td></td><td>months ended</td><td></td><td>months ended</td></tr>
<tr><td></td><td>June 30, 2026</td><td></td><td>March 31, 2026</td><td></td><td>December 31, 2025</td><td></td><td>September 30, 2025</td><td></td><td>June 30, 2025</td></tr>
<tr><td>Interest Income</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Interest and Fees on Loans</td><td>$ 11,650,836</td><td></td><td>$ 11,276,251</td><td></td><td>$ 11,337,250</td><td></td><td>$ 11,671,310</td><td></td><td>$ 11,673,927</td></tr>
<tr><td>Interest on Investment Securities</td><td>1,787,268</td><td></td><td>1,773,078</td><td></td><td>2,224,322</td><td></td><td>2,307,732</td><td></td><td>2,450,914</td></tr>
<tr><td>Interest on Deposits with Other Banks</td><td>285,510</td><td></td><td>703,390</td><td></td><td>214,396</td><td></td><td>507,622</td><td></td><td>750,610</td></tr>
<tr><td>Total Interest Income</td><td>13,723,614</td><td></td><td>13,752,719</td><td></td><td>13,775,968</td><td></td><td>14,486,664</td><td></td><td>14,875,451</td></tr>
<tr><td>Interest Expense</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Interest on Deposits</td><td>6,151,712</td><td></td><td>6,340,041</td><td></td><td>6,260,656</td><td></td><td>7,036,552</td><td></td><td>7,275,073</td></tr>
<tr><td>Interest on Borrowings</td><td>592,778</td><td></td><td>517,291</td><td></td><td>818,943</td><td></td><td>701,474</td><td></td><td>724,216</td></tr>
<tr><td>Total Interest Expense</td><td>6,744,490</td><td></td><td>6,857,332</td><td></td><td>7,079,599</td><td></td><td>7,738,026</td><td></td><td>7,999,289</td></tr>
<tr><td>Net Interest Income</td><td>6,979,124</td><td></td><td>6,895,387</td><td></td><td>6,696,369</td><td></td><td>6,748,638</td><td></td><td>6,876,162</td></tr>
<tr><td>Provision/(Recovery) for Loan Losses</td><td>538,805</td><td></td><td>59,336</td><td></td><td>6,941,897</td><td></td><td>496,824</td><td></td><td>688,865</td></tr>
<tr><td>Net Interest Income After</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Provision for Loan Losses</td><td>6,440,319</td><td></td><td>6,836,051</td><td></td><td>(245,528)</td><td></td><td>6,251,814</td><td></td><td>6,187,297</td></tr>
<tr><td>Non-Interest Income</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Mortgage Loan Gain-on-Sale and Fee Revenue</td><td>1,079,890</td><td></td><td>942,257</td><td></td><td>680,766</td><td></td><td>718,684</td><td></td><td>797,759</td></tr>
<tr><td>SBA Gain-on-Sale Revenue</td><td>-</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td></tr>
<tr><td>Service Charges and Other Income</td><td>327,093</td><td></td><td>220,740</td><td></td><td>246,568</td><td></td><td>453,981</td><td></td><td>270,230</td></tr>
<tr><td>Servicing Income</td><td>16,001</td><td></td><td>17,493</td><td></td><td>18,303</td><td></td><td>19,060</td><td></td><td>21,045</td></tr>
<tr><td>Increase in Cash Surrender Value of Bank-</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>owned Life Insurance</td><td>235,334</td><td></td><td>230,899</td><td></td><td>233,820</td><td></td><td>231,549</td><td></td><td>223,061</td></tr>
<tr><td>Total Non-interest Income</td><td>1,658,318</td><td></td><td>1,411,389</td><td></td><td>1,179,457</td><td></td><td>1,423,274</td><td></td><td>1,312,095</td></tr>
<tr><td>Total Revenue</td><td>8,637,442</td><td></td><td>8,306,776</td><td></td><td>7,875,826</td><td></td><td>8,171,912</td><td></td><td>8,188,257</td></tr>
<tr><td>Non-Interest Expenses</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Officer and Employee Compensation</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>and Benefits</td><td>4,373,967</td><td></td><td>4,403,621</td><td></td><td>3,562,780</td><td></td><td>4,067,037</td><td></td><td>3,752,761</td></tr>
<tr><td>Occupancy Expense</td><td>375,936</td><td></td><td>364,940</td><td></td><td>239,846</td><td></td><td>246,378</td><td></td><td>244,279</td></tr>
<tr><td>Equipment and Depreciation Expense</td><td>11,336</td><td></td><td>10,712</td><td></td><td>12,898</td><td></td><td>16,039</td><td></td><td>16,619</td></tr>
<tr><td>Insurance Expense</td><td>245,402</td><td></td><td>206,599</td><td></td><td>126,852</td><td></td><td>244,170</td><td></td><td>220,346</td></tr>
<tr><td>Professional Fees</td><td>439,501</td><td></td><td>346,305</td><td></td><td>375,040</td><td></td><td>291,975</td><td></td><td>559,904</td></tr>
<tr><td>Data and Item Processing</td><td>587,093</td><td></td><td>530,962</td><td></td><td>523,717</td><td></td><td>540,506</td><td></td><td>595,492</td></tr>
<tr><td>Advertising</td><td>109,791</td><td></td><td>81,600</td><td></td><td>63,476</td><td></td><td>112,566</td><td></td><td>151,676</td></tr>
<tr><td>Franchise Taxes and State Assessment Fees</td><td>329,846</td><td></td><td>326,569</td><td></td><td>324,569</td><td></td><td>334,422</td><td></td><td>314,444</td></tr>
<tr><td>Mortgage Fees and Settlements</td><td>153,051</td><td></td><td>74,839</td><td></td><td>70,037</td><td></td><td>106,266</td><td></td><td>99,819</td></tr>
<tr><td>Other Operating Expense</td><td>1,119,074</td><td></td><td>455,395</td><td></td><td>315,610</td><td></td><td>368,343</td><td></td><td>396,213</td></tr>
<tr><td>Total Non-interest Expenses</td><td>7,744,997</td><td></td><td>6,801,542</td><td></td><td>5,614,825</td><td></td><td>6,327,702</td><td></td><td>6,351,552</td></tr>
<tr><td>Income Before Income Taxes</td><td>353,640</td><td></td><td>1,445,898</td><td></td><td>(4,680,896)</td><td></td><td>1,347,386</td><td></td><td>1,147,840</td></tr>
<tr><td>Income Tax Expense/(Benefit)</td><td>64,019</td><td></td><td>285,560</td><td></td><td>(1,112,923)</td><td></td><td>224,456</td><td></td><td>347,943</td></tr>
<tr><td>Net Income (Loss)</td><td>$ 289,621</td><td></td><td>$ 1,160,338</td><td></td><td>$ (3,567,973)</td><td></td><td>$ 1,122,930</td><td></td><td>$ 799,897</td></tr>
<tr><td>Earnings (Loss) per Common Share - Basic</td><td>$ 0.04</td><td></td><td>$ 0.16</td><td></td><td>$ (0.50)</td><td></td><td>$ 0.16</td><td></td><td>$ 0.11</td></tr>
<tr><td>Earnings (Loss) per Common Share - Diluted</td><td>$ 0.04</td><td></td><td>$ 0.16</td><td></td><td>$ (0.50)</td><td></td><td>$ 0.16</td><td></td><td>$ 0.11</td></tr>
<tr><td>Weighted-Average Common Shares</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Outstanding - Basic</td><td>7,098,594</td><td></td><td>7,104,820</td><td></td><td>7,121,482</td><td></td><td>7,134,446</td><td></td><td>7,137,779</td></tr>
<tr><td>Weighted-Average Common Shares</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Outstanding - Diluted</td><td>7,124,543</td><td></td><td>7,174,318</td><td></td><td>7,183,791</td><td></td><td>7,184,688</td><td></td><td>7,140,491</td></tr>
</table>
<table>
<tr><td>Selected Financial Data by Quarter Ended:</td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>(Unaudited)</td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Balance Sheet Ratios</td><td>June 30, 2026</td><td>March 31, 2026</td><td>December 31, 2025</td><td>September 30, 2025</td><td>June 30, 2025</td></tr>
<tr><td>Loans held-for-investment to Deposits</td><td>87.29 %</td><td>84.04 %</td><td>83.41 %</td><td>86.72 %</td><td>80.83 %</td></tr>
<tr><td>Income Statement Ratios (Quarterly)</td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Return on Average Assets (ROAA)</td><td>0.11 %</td><td>0.44 %</td><td>-1.37 %</td><td>0.42 %</td><td>0.29 %</td></tr>
<tr><td>Return on Average Equity (ROAE)</td><td>1.38 %</td><td>5.57 %</td><td>-15.96 %</td><td>5.57 %</td><td>3.97 %</td></tr>
<tr><td>Efficiency Ratio</td><td>89.67 %</td><td>81.88 %</td><td>71.29 %</td><td>77.43 %</td><td>77.57 %</td></tr>
<tr><td>Net Interest Margin</td><td>2.83 %</td><td>2.73 %</td><td>2.70 %</td><td>2.66 %</td><td>2.66 %</td></tr>
<tr><td>Yield on Average Earning Assets</td><td>5.57 %</td><td>5.44 %</td><td>5.55 %</td><td>5.72 %</td><td>5.73 %</td></tr>
<tr><td>Yield on Securities</td><td>4.01 %</td><td>3.97 %</td><td>4.23 %</td><td>4.29 %</td><td>4.39 %</td></tr>
<tr><td>Yield on Loans</td><td>6.01 %</td><td>5.97 %</td><td>5.98 %</td><td>6.22 %</td><td>6.20 %</td></tr>
<tr><td>Cost of Funds</td><td>2.85 %</td><td>2.84 %</td><td>2.99 %</td><td>3.19 %</td><td>3.19 %</td></tr>
<tr><td>Noninterest income to Total Revenue</td><td>19.20 %</td><td>16.99 %</td><td>14.98 %</td><td>17.42 %</td><td>16.02 %</td></tr>
<tr><td>Liquidity Ratios</td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Uninsured Deposits to Total Deposits</td><td>24.90 %</td><td>27.11 %</td><td>29.43 %</td><td>24.51 %</td><td>22.51 %</td></tr>
<tr><td>Total Liquidity to Uninsured Deposits</td><td>118.71 %</td><td>117.18 %</td><td>130.31 %</td><td>136.91 %</td><td>167.83 %</td></tr>
<tr><td>Total Liquidity to Unfunded Commitments, CDs and Borrowings maturing in next 30 days</td><td>166.82 %</td><td>206.16 %</td><td>251.78 %</td><td>209.14 %</td><td>252.65 %</td></tr>
<tr><td>Tangible Common Equity Ratio</td><td>8.20 %</td><td>8.00 %</td><td>7.91 %</td><td>8.45 %</td><td>7.85 %</td></tr>
<tr><td>Tangible Common Equity Ratio (adjusted for unrealized losses on HTM securities)</td><td>8.01 %</td><td>7.82 %</td><td>7.76 %</td><td>8.27 %</td><td>7.64 %</td></tr>
<tr><td>Available -for-Sale securities (as % of total securities)</td><td>89.41 %</td><td>89.58 %</td><td>89.17 %</td><td>90.64 %</td><td>90.87 %</td></tr>
<tr><td>Per Share Data</td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Tangible Book Value</td><td>$12.20</td><td>$12.08</td><td>$12.05</td><td>$12.45</td><td>$12.01</td></tr>
<tr><td>Tangible Book Value (ex AOCI)</td><td>$14.29</td><td>$14.18</td><td>$14.08</td><td>$14.58</td><td>$14.39</td></tr>
<tr><td>Share Price Data</td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Closing Price</td><td>$12.15</td><td>$11.90</td><td>$11.83</td><td>$11.52</td><td>$11.26</td></tr>
<tr><td>Book Value Multiple</td><td>100 %</td><td>99 %</td><td>98 %</td><td>93 %</td><td>94 %</td></tr>
<tr><td>Common Stock Data</td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Outstanding Shares at End of Period</td><td>6,978,754</td><td>6,973,747</td><td>6,984,013</td><td>7,002,103</td><td>7,002,103</td></tr>
<tr><td>Weighted Average shares outstanding, basic</td><td>7,098,594</td><td>7,104,820</td><td>7,136,456</td><td>7,134,446</td><td>7,137,779</td></tr>
<tr><td>Weighted Average shares outstanding, diluted</td><td>7,124,543</td><td>7,174,318</td><td>7,193,284</td><td>7,184,688</td><td>7,140,491</td></tr>
<tr><td>Capital Ratios (Bank Only)</td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Tier 1 Leverage ratio</td><td>11.06 %</td><td>10.70 %</td><td>11.05 %</td><td>11.23 %</td><td>10.66 %</td></tr>
<tr><td>Common Equity Tier 1 ratio</td><td>13.66 %</td><td>13.50 %</td><td>13.82 %</td><td>14.64 %</td><td>14.30 %</td></tr>
<tr><td>Tier 1 Risk Based Capital ratio</td><td>13.66 %</td><td>13.50 %</td><td>13.82 %</td><td>14.64 %</td><td>14.30 %</td></tr>
<tr><td>Total Risk Based Capital ratio</td><td>14.63 %</td><td>14.42 %</td><td>15.08 %</td><td>15.53 %</td><td>15.20 %</td></tr>
<tr><td>Credit Quality</td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Net Charge-offs to Average Loans</td><td>0.02 %</td><td>0.81 %</td><td>0.03 %</td><td>0.13 %</td><td>0.01 %</td></tr>
<tr><td>Total Non-performing Loans to loans held-for-investment</td><td>3.32 %</td><td>2.46 %</td><td>3.51 %</td><td>2.30 %</td><td>1.45 %</td></tr>
<tr><td>Total Non-performing Assets to Total Assets</td><td>2.57 %</td><td>1.95 %</td><td>2.51 %</td><td>1.65 %</td><td>0.98 %</td></tr>
<tr><td>Nonaccrual Loans to loans held-for-investment</td><td>3.32 %</td><td>2.50 %</td><td>3.51 %</td><td>2.30 %</td><td>1.45 %</td></tr>
<tr><td>Provision for Loan Losses</td><td>$538,805</td><td>$59,336</td><td>$6,941,897</td><td>$496,824</td><td>$688,865</td></tr>
<tr><td>Allowance for Loan Losses to net loans held-for-investment</td><td>1.06 %</td><td>1.00 %</td><td>1.82 %</td><td>0.96 %</td><td>0.96 %</td></tr>
<tr><td>Allowance for Loan Losses to net loans held-for-investment (ex PPP loans)</td><td>1.06 %</td><td>1.00 %</td><td>1.82 %</td><td>0.96 %</td><td>0.96 %</td></tr>
</table>
</body></html>
'''

# https://www.prnewswire.com/news-releases/bank-of-south-carolina-corporation-announces-second-quarter-earnings-302821931.html
BKSC_2Q26 = '''
<html><body>

<table>
<tr><td>Selected Condensed Consolidated Financial Data (Unaudited)</td></tr>
<tr><td></td><td>For the Three Months Ended</td></tr>
<tr><td></td><td>June 30, 2026</td><td>March 31, 2026</td><td>December 31, 2025</td><td>September 30, 2025</td><td>June 30, 2025</td></tr>
<tr><td>Total Interest and Fee Income</td><td>$ 7,259,871</td><td>$ 6,869,250</td><td>$ 7,139,722</td><td>$ 7,317,405</td><td>$ 7,202,647</td></tr>
<tr><td>Total Interest Expense</td><td>893,168</td><td>1,008,229</td><td>1,207,326</td><td>1,303,263</td><td>1,304,294</td></tr>
<tr><td>Net Interest Income</td><td>6,366,703</td><td>5,861,021</td><td>5,932,396</td><td>6,014,142</td><td>5,898,353</td></tr>
<tr><td>Provision for Credit Losses</td><td>150,000</td><td>50,000</td><td>200,000</td><td>250,000</td><td>100,000</td></tr>
<tr><td>Net Interest Income After Provision for Credit Losses</td><td>6,216,703</td><td>5,811,021</td><td>5,732,396</td><td>5,764,142</td><td>5,798,353</td></tr>
<tr><td>Total Other Income</td><td>467,782</td><td>414,100</td><td>448,178</td><td>519,500</td><td>421,104</td></tr>
<tr><td>Total Other Expense</td><td>3,673,251</td><td>3,627,528</td><td>3,765,414</td><td>3,516,683</td><td>3,663,556</td></tr>
<tr><td>Income Before Income Tax Expense</td><td>3,011,234</td><td>2,597,593</td><td>2,415,160</td><td>2,766,959</td><td>2,555,901</td></tr>
<tr><td>Income Tax Expense</td><td>692,301</td><td>586,674</td><td>401,879</td><td>623,319</td><td>599,746</td></tr>
<tr><td>Net Income</td><td>$ 2,318,933</td><td>$ 2,010,919</td><td>$ 2,013,281</td><td>$ 2,143,640</td><td>$ 1,956,155</td></tr>
<tr><td>Earnings Per Share - Basic</td><td>$ 0.43</td><td>$ 0.37</td><td>$ 0.37</td><td>$ 0.39</td><td>$ 0.36</td></tr>
<tr><td>Earnings Per Share - Diluted</td><td>$ 0.42</td><td>$ 0.36</td><td>$ 0.36</td><td>$ 0.38</td><td>$ 0.35</td></tr>
<tr><td>Return on Average Assets</td><td>1.64 %</td><td>1.46 %</td><td>1.39 %</td><td>1.46 %</td><td>1.37 %</td></tr>
<tr><td>Return on Average Equity</td><td>15.16 %</td><td>13.39 %</td><td>13.28 %</td><td>14.58 %</td><td>13.95 %</td></tr>
<tr><td>Net Interest Margin</td><td>4.71 %</td><td>4.43 %</td><td>4.28 %</td><td>4.30 %</td><td>4.33 %</td></tr>
<tr><td>Efficiency Ratio</td><td>53.75 %</td><td>57.81 %</td><td>59.01 %</td><td>53.82 %</td><td>57.97 %</td></tr>
<tr><td>Common Stock Shares Outstanding</td><td>5,338,872</td><td>5,379,779</td><td>5,399,732</td><td>5,420,099</td><td>5,422,475</td></tr>
<tr><td>Book Value Per Share</td><td>$ 11.43</td><td>$ 11.22</td><td>$ 11.14</td><td>$ 10.88</td><td>$ 10.47</td></tr>
<tr><td>Community Bank Leverage Ratio</td><td>11.63 %</td><td>11.71 %</td><td>11.33 %</td><td>11.19 %</td><td>11.19 %</td></tr>
<tr><td>% Loans Past Due &gt; 30 Days</td><td>0.98 %</td><td>0.43 %</td><td>0.76 %</td><td>0.21 %</td><td>0.29 %</td></tr>
<tr><td>Allowance for Credit Losses as a % of Total Loans</td><td>1.22 %</td><td>1.19 %</td><td>1.18 %</td><td>1.16 %</td><td>1.06 %</td></tr>
<tr><td>Quarterly Averages:</td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Total Assets</td><td>$ 567,201,707</td><td>$ 560,348,116</td><td>$ 575,001,866</td><td>$ 580,830,205</td><td>$ 572,875,953</td></tr>
<tr><td>Total Loans</td><td>$ 371,135,923</td><td>$ 361,591,069</td><td>$ 361,339,396</td><td>$ 360,100,453</td><td>$ 363,065,921</td></tr>
<tr><td>Total Deposits</td><td>$ 491,625,349</td><td>$ 484,406,399</td><td>$ 498,995,120</td><td>$ 506,765,581</td><td>$ 501,217,309</td></tr>
<tr><td>Total Shareholders&#x27; Equity</td><td>$ 61,371,224</td><td>$ 60,908,702</td><td>$ 60,159,434</td><td>$ 58,315,231</td><td>$ 56,244,469</td></tr>
</table>
</body></html>
'''

# https://www.prnewswire.com/news-releases/potomac-bancshares-reports-28-increase-in-first-quarter-results-302756335.html
PTBS_1Q26 = '''
<html><body>

<table>
<tr><td></td><td>Q1 2026</td><td>Q4 2025</td><td>Q1 2025</td></tr>
<tr><td>Net Income</td><td>$3,044</td><td>$2,372</td><td>$2,188</td></tr>
<tr><td>EPS (basic and diluted)</td><td>$0.73</td><td>$0.57</td><td>$0.53</td></tr>
<tr><td>ROA</td><td>1.28 %</td><td>0.97 %</td><td>1.01 %</td></tr>
<tr><td>ROE</td><td>14.68 %</td><td>11.51 %</td><td>11.88 %</td></tr>
<tr><td>Non-GAAP Measures 1 :</td><td></td><td></td><td></td></tr>
<tr><td>Adj. Net Income</td><td>$2,865</td><td>$2,174</td><td>$2,188</td></tr>
<tr><td>Adj. EPS (basic and diluted)</td><td>$0.69</td><td>$0.52</td><td>$0.53</td></tr>
<tr><td>Adj. ROA</td><td>1.21 %</td><td>0.89 %</td><td>1.01 %</td></tr>
<tr><td>Adj. ROE</td><td>13.82 %</td><td>10.55 %</td><td>11.88 %</td></tr>
<tr><td>Adj. Pre-Provision, Pre-Tax Earnings</td><td>$3,860</td><td>$3,086</td><td>$2,982</td></tr>
<tr><td>Adj. Pre-Provision, Pre-Tax ROA</td><td>1.63 %</td><td>1.26 %</td><td>1.37 %</td></tr>
<tr><td>Net Interest Margin</td><td>3.66 %</td><td>3.55 %</td><td>3.51 %</td></tr>
<tr><td>Efficiency Ratio</td><td>64.84 %</td><td>70.29 %</td><td>67.47 %</td></tr>
</table>
<table>
<tr><td>POTOMAC BANCSHARES, INC.</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Performance Summary</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>(in thousands, except share and per share data)</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>(unaudited)</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td></td><td>For the Three Months Ended</td></tr>
<tr><td></td><td></td><td>March 31,</td><td></td><td>December 31,</td><td></td><td>September 30,</td><td></td><td>June 30,</td><td></td><td>March 31,</td></tr>
<tr><td></td><td></td><td>2026</td><td></td><td>2025</td><td></td><td>2025</td><td></td><td>2025</td><td></td><td>2025</td></tr>
<tr><td>Income Statement</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Interest and dividend income:</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Interest and fees on loans</td><td></td><td>$ 10,485</td><td></td><td>$ 10,727</td><td></td><td>$ 10,447</td><td></td><td>$ 9,682</td><td></td><td>$ 9,501</td></tr>
<tr><td>Taxable interest on securities</td><td></td><td>796</td><td></td><td>732</td><td></td><td>709</td><td></td><td>710</td><td></td><td>715</td></tr>
<tr><td>Tax-exempt interest on securities</td><td></td><td>29</td><td></td><td>29</td><td></td><td>30</td><td></td><td>28</td><td></td><td>29</td></tr>
<tr><td>Other interest and dividends</td><td></td><td>833</td><td></td><td>1,285</td><td></td><td>1,060</td><td></td><td>989</td><td></td><td>674</td></tr>
<tr><td>Total interest and dividend income</td><td></td><td>$ 12,143</td><td></td><td>$ 12,773</td><td></td><td>$ 12,246</td><td></td><td>$ 11,409</td><td></td><td>$ 10,919</td></tr>
<tr><td>Interest expense:</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Interest on deposits</td><td></td><td>$ 3,243</td><td></td><td>$ 3,445</td><td></td><td>$ 3,709</td><td></td><td>$ 3,324</td><td></td><td>$ 3,105</td></tr>
<tr><td>Interest on short term borrowings</td><td></td><td>3</td><td></td><td>8</td><td></td><td>9</td><td></td><td>2</td><td></td><td>6</td></tr>
<tr><td>Interest on long term borrowings</td><td></td><td>290</td><td></td><td>312</td><td></td><td>312</td><td></td><td>309</td><td></td><td>313</td></tr>
<tr><td>Interest on subordinated debt</td><td></td><td>214</td><td></td><td>224</td><td></td><td>152</td><td></td><td>140</td><td></td><td>141</td></tr>
<tr><td>Total interest expense</td><td></td><td>$ 3,750</td><td></td><td>$ 3,989</td><td></td><td>$ 4,182</td><td></td><td>$ 3,775</td><td></td><td>$ 3,565</td></tr>
<tr><td>Net interest income</td><td></td><td>$ 8,393</td><td></td><td>$ 8,784</td><td></td><td>$ 8,064</td><td></td><td>$ 7,634</td><td></td><td>$ 7,354</td></tr>
<tr><td>Provision for credit losses</td><td></td><td>200</td><td></td><td>250</td><td></td><td>200</td><td></td><td>225</td><td></td><td>250</td></tr>
<tr><td>Net interest income after provision for credit losses</td><td></td><td>$ 8,193</td><td></td><td>$ 8,534</td><td></td><td>$ 7,864</td><td></td><td>$ 7,409</td><td></td><td>$ 7,104</td></tr>
<tr><td>Noninterest Income:</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Wealth and investments</td><td></td><td>$ 745</td><td></td><td>$ 536</td><td></td><td>$ 525</td><td></td><td>$ 498</td><td></td><td>$ 505</td></tr>
<tr><td>Service charges on deposit accounts</td><td></td><td>234</td><td></td><td>228</td><td></td><td>217</td><td></td><td>225</td><td></td><td>260</td></tr>
<tr><td>Gains / fees on sale of mortgage loans</td><td></td><td>494</td><td></td><td>443</td><td></td><td>408</td><td></td><td>351</td><td></td><td>247</td></tr>
<tr><td>ATM and check card fees</td><td></td><td>499</td><td></td><td>549</td><td></td><td>543</td><td></td><td>518</td><td></td><td>475</td></tr>
<tr><td>Income from bank owned life insurance</td><td></td><td>101</td><td></td><td>102</td><td></td><td>102</td><td></td><td>100</td><td></td><td>97</td></tr>
<tr><td>Net loss on disposal of premises &amp; equipment</td><td></td><td>(9)</td><td></td><td>(9)</td><td></td><td>(1)</td><td></td><td>-</td><td></td><td>(2)</td></tr>
<tr><td>Net gain on sale of SBA loans</td><td></td><td>408</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td></tr>
<tr><td>Other operating income</td><td></td><td>368</td><td></td><td>197</td><td></td><td>120</td><td></td><td>74</td><td></td><td>247</td></tr>
<tr><td>Total noninterest income</td><td></td><td>$ 2,840</td><td></td><td>$ 2,046</td><td></td><td>$ 1,914</td><td></td><td>$ 1,766</td><td></td><td>$ 1,829</td></tr>
<tr><td>Noninterest expenses:</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Salaries and employee benefits</td><td></td><td>$ 4,049</td><td></td><td>$ 4,143</td><td></td><td>$ 3,717</td><td></td><td>$ 3,742</td><td></td><td>$ 3,350</td></tr>
<tr><td>Occupancy</td><td></td><td>334</td><td></td><td>339</td><td></td><td>310</td><td></td><td>310</td><td></td><td>344</td></tr>
<tr><td>Equipment</td><td></td><td>269</td><td></td><td>294</td><td></td><td>351</td><td></td><td>344</td><td></td><td>376</td></tr>
<tr><td>Accounting, audit, and compliance</td><td></td><td>73</td><td></td><td>72</td><td></td><td>72</td><td></td><td>70</td><td></td><td>69</td></tr>
<tr><td>Marketing</td><td></td><td>147</td><td></td><td>182</td><td></td><td>115</td><td></td><td>112</td><td></td><td>118</td></tr>
<tr><td>Data processing</td><td></td><td>485</td><td></td><td>442</td><td></td><td>413</td><td></td><td>453</td><td></td><td>452</td></tr>
<tr><td>FDIC assessment</td><td></td><td>108</td><td></td><td>107</td><td></td><td>111</td><td></td><td>104</td><td></td><td>99</td></tr>
<tr><td>Other professional fees</td><td></td><td>135</td><td></td><td>313</td><td></td><td>208</td><td></td><td>140</td><td></td><td>132</td></tr>
<tr><td>Trust professional fees</td><td></td><td>206</td><td></td><td>180</td><td></td><td>190</td><td></td><td>144</td><td></td><td>171</td></tr>
<tr><td>Director and committee fees</td><td></td><td>126</td><td></td><td>120</td><td></td><td>93</td><td></td><td>68</td><td></td><td>97</td></tr>
<tr><td>Legal fees</td><td></td><td>17</td><td></td><td>32</td><td></td><td>47</td><td></td><td>23</td><td></td><td>33</td></tr>
<tr><td>Supplies</td><td></td><td>89</td><td></td><td>61</td><td></td><td>55</td><td></td><td>66</td><td></td><td>79</td></tr>
<tr><td>Communications</td><td></td><td>121</td><td></td><td>120</td><td></td><td>119</td><td></td><td>112</td><td></td><td>112</td></tr>
<tr><td>ATM and check card expense</td><td></td><td>273</td><td></td><td>282</td><td></td><td>269</td><td></td><td>264</td><td></td><td>240</td></tr>
<tr><td>Other operating expenses</td><td></td><td>714</td><td></td><td>806</td><td></td><td>715</td><td></td><td>547</td><td></td><td>529</td></tr>
<tr><td>Total noninterest expenses</td><td></td><td>$ 7,146</td><td></td><td>$ 7,493</td><td></td><td>$ 6,785</td><td></td><td>$ 6,499</td><td></td><td>$ 6,201</td></tr>
<tr><td>Income before income tax expense</td><td></td><td>$ 3,887</td><td></td><td>$ 3,087</td><td></td><td>$ 2,993</td><td></td><td>$ 2,676</td><td></td><td>$ 2,732</td></tr>
<tr><td>Income tax expense</td><td></td><td>843</td><td></td><td>715</td><td></td><td>671</td><td></td><td>602</td><td></td><td>544</td></tr>
<tr><td>Net income</td><td></td><td>$ 3,044</td><td></td><td>$ 2,372</td><td></td><td>$ 2,322</td><td></td><td>$ 2,074</td><td></td><td>$ 2,188</td></tr>
</table>
<table>
<tr><td>POTOMAC BANCSHARES, INC.</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Performance Summary</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>(in thousands, except share and per share data)</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>(unaudited)</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td></td><td></td><td>For the Period Ended</td></tr>
<tr><td></td><td></td><td>March 31,</td><td></td><td>December 31,</td><td></td><td>September 30,</td><td></td><td>June 30,</td><td></td><td>March 31,</td></tr>
<tr><td></td><td></td><td>2026</td><td></td><td>2025</td><td></td><td>2025</td><td></td><td>2025</td><td></td><td>2025</td></tr>
<tr><td>Balance Sheet</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Cash and due from banks</td><td></td><td>$ 6,133</td><td></td><td>$ 3,603</td><td></td><td>$ 4,648</td><td></td><td>$ 4,638</td><td></td><td>$ 4,673</td></tr>
<tr><td>Interest-bearing deposits in other financial institutions</td><td></td><td>87,754</td><td></td><td>76,046</td><td></td><td>115,174</td><td></td><td>67,636</td><td></td><td>66,844</td></tr>
<tr><td>Cash and cash equivalents</td><td></td><td>$ 93,887</td><td></td><td>$ 79,649</td><td></td><td>$ 119,822</td><td></td><td>$ 72,274</td><td></td><td>$ 71,517</td></tr>
<tr><td>Securities available for sale, at fair value</td><td></td><td>92,713</td><td></td><td>80,905</td><td></td><td>77,935</td><td></td><td>76,787</td><td></td><td>76,763</td></tr>
<tr><td>Equity securities, at fair value</td><td></td><td>280</td><td></td><td>258</td><td></td><td>278</td><td></td><td>246</td><td></td><td>243</td></tr>
<tr><td>Restricted securities</td><td></td><td>1,852</td><td></td><td>1,932</td><td></td><td>1,932</td><td></td><td>2,037</td><td></td><td>2,023</td></tr>
<tr><td>Loans held for sale</td><td></td><td>1,771</td><td></td><td>2,804</td><td></td><td>2,946</td><td></td><td>5,682</td><td></td><td>2,234</td></tr>
<tr><td>Loans, net of allowance for credit losses</td><td></td><td>750,548</td><td></td><td>743,808</td><td></td><td>724,611</td><td></td><td>729,065</td><td></td><td>709,160</td></tr>
<tr><td>Premises and equipment, net</td><td></td><td>8,734</td><td></td><td>8,759</td><td></td><td>8,164</td><td></td><td>8,107</td><td></td><td>8,240</td></tr>
<tr><td>Accrued interest receivable</td><td></td><td>2,719</td><td></td><td>2,309</td><td></td><td>2,592</td><td></td><td>2,439</td><td></td><td>2,478</td></tr>
<tr><td>Bank owned life insurance</td><td></td><td>14,002</td><td></td><td>14,378</td><td></td><td>14,275</td><td></td><td>14,174</td><td></td><td>14,074</td></tr>
<tr><td>Other assets</td><td></td><td>9,340</td><td></td><td>9,482</td><td></td><td>9,456</td><td></td><td>9,528</td><td></td><td>8,851</td></tr>
<tr><td>Total assets</td><td></td><td>$ 975,846</td><td></td><td>$ 944,284</td><td></td><td>$ 962,011</td><td></td><td>$ 920,339</td><td></td><td>$ 895,583</td></tr>
<tr><td>Noninterest-bearing demand deposits</td><td></td><td>$ 187,715</td><td></td><td>$ 183,461</td><td></td><td>$ 204,355</td><td></td><td>$ 176,708</td><td></td><td>$ 186,182</td></tr>
<tr><td>Savings and interest-bearing demand deposits</td><td></td><td>657,665</td><td></td><td>629,568</td><td></td><td>629,062</td><td></td><td>618,155</td><td></td><td>586,200</td></tr>
<tr><td>Total deposits</td><td></td><td>$ 845,380</td><td></td><td>$ 813,029</td><td></td><td>$ 833,417</td><td></td><td>$ 794,863</td><td></td><td>$ 772,382</td></tr>
<tr><td>Short term borrowings</td><td></td><td>2,241</td><td></td><td>2,451</td><td></td><td>3,013</td><td></td><td>2,793</td><td></td><td>3,052</td></tr>
<tr><td>Long term borrowings</td><td></td><td>27,000</td><td></td><td>29,000</td><td></td><td>29,000</td><td></td><td>29,000</td><td></td><td>29,000</td></tr>
<tr><td>Subordinated debt</td><td></td><td>10,000</td><td></td><td>10,000</td><td></td><td>10,000</td><td></td><td>9,989</td><td></td><td>9,973</td></tr>
<tr><td>Accrued interest payable</td><td></td><td>936</td><td></td><td>1,052</td><td></td><td>1,037</td><td></td><td>1,148</td><td></td><td>987</td></tr>
<tr><td>Other liabilities</td><td></td><td>5,652</td><td></td><td>6,309</td><td></td><td>5,185</td><td></td><td>5,056</td><td></td><td>4,140</td></tr>
<tr><td>Total liabilities</td><td></td><td>$ 891,209</td><td></td><td>$ 861,841</td><td></td><td>$ 881,652</td><td></td><td>$ 842,849</td><td></td><td>$ 819,534</td></tr>
<tr><td>Common stock</td><td></td><td>$ 4,493</td><td></td><td>$ 4,493</td><td></td><td>$ 4,493</td><td></td><td>$ 4,493</td><td></td><td>$ 4,493</td></tr>
<tr><td>Surplus</td><td></td><td>14,547</td><td></td><td>14,547</td><td></td><td>14,547</td><td></td><td>14,547</td><td></td><td>14,547</td></tr>
<tr><td>Retained Earnings</td><td></td><td>73,154</td><td></td><td>70,649</td><td></td><td>68,815</td><td></td><td>67,032</td><td></td><td>65,497</td></tr>
<tr><td>Accumulated other comprehensive (loss), net</td><td></td><td>(4,063)</td><td></td><td>(3,752)</td><td></td><td>(4,002)</td><td></td><td>(5,088)</td><td></td><td>(4,994)</td></tr>
<tr><td></td><td></td><td>$ 88,131</td><td></td><td>$ 85,937</td><td></td><td>$ 83,853</td><td></td><td>$ 80,984</td><td></td><td>$ 79,543</td></tr>
<tr><td>Less cost of shares acquired for the treasury</td><td></td><td>(3,494)</td><td></td><td>(3,494)</td><td></td><td>(3,494)</td><td></td><td>(3,494)</td><td></td><td>(3,494)</td></tr>
<tr><td>Total shareholders&#x27; equity</td><td></td><td>$ 84,637</td><td></td><td>$ 82,443</td><td></td><td>$ 80,359</td><td></td><td>$ 77,490</td><td></td><td>$ 76,049</td></tr>
<tr><td>Total liabilities and shareholders&#x27; equity</td><td></td><td>$ 975,846</td><td></td><td>$ 944,284</td><td></td><td>$ 962,011</td><td></td><td>$ 920,339</td><td></td><td>$ 895,583</td></tr>
<tr><td>Loan Data</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Construction and land development</td><td></td><td>$ 37,751</td><td></td><td>$ 45,537</td><td></td><td>$ 45,979</td><td></td><td>$ 46,882</td><td></td><td>$ 42,954</td></tr>
<tr><td>Secured by farmland</td><td></td><td>7,435</td><td></td><td>7,509</td><td></td><td>7,594</td><td></td><td>6,732</td><td></td><td>6,707</td></tr>
<tr><td>Secured by 1-4 family residential properties</td><td></td><td>270,027</td><td></td><td>258,467</td><td></td><td>256,974</td><td></td><td>253,798</td><td></td><td>250,436</td></tr>
<tr><td>Secured by multifamily residential properties</td><td></td><td>38,205</td><td></td><td>39,280</td><td></td><td>39,928</td><td></td><td>39,246</td><td></td><td>28,573</td></tr>
<tr><td>Secured by owner-occupied nonfarm nonresidential properties</td><td></td><td>114,770</td><td></td><td>114,078</td><td></td><td>117,053</td><td></td><td>118,883</td><td></td><td>119,341</td></tr>
<tr><td>Secured by other nonfarm nonresidential properties</td><td></td><td>217,282</td><td></td><td>205,548</td><td></td><td>188,227</td><td></td><td>197,561</td><td></td><td>197,039</td></tr>
<tr><td>Loans to farmers (except secured by real estate)</td><td></td><td>109</td><td></td><td>120</td><td></td><td>128</td><td></td><td>118</td><td></td><td>237</td></tr>
<tr><td>Commercial and industrial loans (except those secured by real estate)</td><td></td><td>63,517</td><td></td><td>72,158</td><td></td><td>66,965</td><td></td><td>63,763</td><td></td><td>61,348</td></tr>
<tr><td>Consumer installment loans</td><td></td><td>2,859</td><td></td><td>2,757</td><td></td><td>2,845</td><td></td><td>2,860</td><td></td><td>2,910</td></tr>
<tr><td>Deposit overdraft</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td></tr>
<tr><td>All other loans</td><td></td><td>6,565</td><td></td><td>6,150</td><td></td><td>6,424</td><td></td><td>6,581</td><td></td><td>6,795</td></tr>
<tr><td>Total loans</td><td></td><td>$ 758,520</td><td></td><td>$ 751,604</td><td></td><td>$ 732,117</td><td></td><td>$ 736,424</td><td></td><td>$ 716,340</td></tr>
<tr><td>Allowance for credit losses</td><td></td><td>(7,972)</td><td></td><td>(7,796)</td><td></td><td>(7,506)</td><td></td><td>(7,359)</td><td></td><td>(7,180)</td></tr>
<tr><td>Loans, net</td><td></td><td>$ 750,548</td><td></td><td>$ 743,808</td><td></td><td>$ 724,611</td><td></td><td>$ 729,065</td><td></td><td>$ 709,160</td></tr>
</table>
<table>
<tr><td>POTOMAC BANCSHARES, INC.</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Performance Summary</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>(in thousands, except share and per share data)</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>(unaudited)</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td></td><td></td><td>As of or For the Three Months Ended</td></tr>
<tr><td></td><td></td><td>March 31,</td><td></td><td>December 31,</td><td></td><td>September 30,</td><td></td><td>June 30,</td><td></td><td>March 31,</td></tr>
<tr><td></td><td></td><td>2026</td><td></td><td>2025</td><td></td><td>2025</td><td></td><td>2025</td><td></td><td>2025</td></tr>
<tr><td>Common Share and Per Common Share Data</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Earnings per common share, basic</td><td></td><td>$ 0.73</td><td></td><td>$ 0.57</td><td></td><td>$ 0.56</td><td></td><td>$ 0.50</td><td></td><td>$ 0.53</td></tr>
<tr><td>Adjusted earnings per common share, basic (1)</td><td></td><td>$ 0.69</td><td></td><td>$ 0.52</td><td></td><td>$ 0.58</td><td></td><td>$ 0.52</td><td></td><td>$ 0.53</td></tr>
<tr><td>Weighted average shares, basic</td><td></td><td>4,144,561</td><td></td><td>4,144,561</td><td></td><td>4,144,561</td><td></td><td>4,144,561</td><td></td><td>4,144,561</td></tr>
<tr><td>Earnings per common share, diluted</td><td></td><td>$ 0.73</td><td></td><td>$ 0.57</td><td></td><td>$ 0.56</td><td></td><td>$ 0.50</td><td></td><td>$ 0.53</td></tr>
<tr><td>Adjusted earnings per common share, diluted (1)</td><td></td><td>$ 0.69</td><td></td><td>$ 0.52</td><td></td><td>$ 0.58</td><td></td><td>$ 0.52</td><td></td><td>$ 0.53</td></tr>
<tr><td>Weighted average shares, diluted</td><td></td><td>4,144,561</td><td></td><td>4,144,561</td><td></td><td>4,144,561</td><td></td><td>4,144,561</td><td></td><td>4,144,561</td></tr>
<tr><td>Shares outstanding at period end</td><td></td><td>4,144,561</td><td></td><td>4,144,561</td><td></td><td>4,144,561</td><td></td><td>4,144,561</td><td></td><td>4,144,561</td></tr>
<tr><td>Tangible book value per share at period end (1)</td><td></td><td>$ 20.42</td><td></td><td>$ 19.89</td><td></td><td>$ 19.39</td><td></td><td>$ 18.70</td><td></td><td>$ 18.35</td></tr>
<tr><td>Cash dividends</td><td></td><td>$ 0.13</td><td></td><td>$ 0.13</td><td></td><td>$ 0.12</td><td></td><td>$ 0.12</td><td></td><td>$ 0.12</td></tr>
<tr><td>Key Performance Ratios</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Return on average assets</td><td></td><td>1.28 %</td><td></td><td>0.97 %</td><td></td><td>0.98 %</td><td></td><td>0.91 %</td><td></td><td>1.01 %</td></tr>
<tr><td>Adjusted return on average assets (1)</td><td></td><td>1.21 %</td><td></td><td>0.89 %</td><td></td><td>1.01 %</td><td></td><td>0.95 %</td><td></td><td>1.01 %</td></tr>
<tr><td>Return on average equity</td><td></td><td>14.68 %</td><td></td><td>11.51 %</td><td></td><td>11.62 %</td><td></td><td>10.83 %</td><td></td><td>11.88 %</td></tr>
<tr><td>Adjusted return on average equity (1)</td><td></td><td>13.82 %</td><td></td><td>10.55 %</td><td></td><td>11.94 %</td><td></td><td>11.27 %</td><td></td><td>11.88 %</td></tr>
<tr><td>Net interest margin (1)</td><td></td><td>3.66 %</td><td></td><td>3.55 %</td><td></td><td>3.54 %</td><td></td><td>3.48 %</td><td></td><td>3.51 %</td></tr>
<tr><td>Efficiency ratio (1)</td><td></td><td>64.84 %</td><td></td><td>70.29 %</td><td></td><td>67.13 %</td><td></td><td>67.96 %</td><td></td><td>67.47 %</td></tr>
<tr><td>Average Balances</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Average assets</td><td></td><td>$ 961,992</td><td></td><td>$ 968,056</td><td></td><td>$ 936,572</td><td></td><td>$ 912,253</td><td></td><td>$ 881,490</td></tr>
<tr><td>Average earning assets</td><td></td><td>930,543</td><td></td><td>937,335</td><td></td><td>905,307</td><td></td><td>881,485</td><td></td><td>850,035</td></tr>
<tr><td>Average shareholders&#x27; equity</td><td></td><td>84,077</td><td></td><td>81,783</td><td></td><td>79,290</td><td></td><td>76,808</td><td></td><td>74,694</td></tr>
<tr><td>Asset Quality</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Loan charge-offs</td><td></td><td>$ 23</td><td></td><td>$ 22</td><td></td><td>$ 23</td><td></td><td>$ 65</td><td></td><td>$ 21</td></tr>
<tr><td>Loan recoveries</td><td></td><td>6</td><td></td><td>4</td><td></td><td>10</td><td></td><td>20</td><td></td><td>20</td></tr>
<tr><td>Net charge-offs</td><td></td><td>17</td><td></td><td>18</td><td></td><td>13</td><td></td><td>45</td><td></td><td>1</td></tr>
<tr><td>Non-accrual loans</td><td></td><td>257</td><td></td><td>-</td><td></td><td>2,138</td><td></td><td>2,244</td><td></td><td>2,245</td></tr>
<tr><td>Other real estate owned, net</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td></tr>
<tr><td>Nonperforming assets (5)</td><td></td><td>257</td><td></td><td>-</td><td></td><td>2,138</td><td></td><td>2,244</td><td></td><td>2,245</td></tr>
<tr><td>Loans 30 to 89 days past due, accruing</td><td></td><td>1,491</td><td></td><td>677</td><td></td><td>694</td><td></td><td>726</td><td></td><td>523</td></tr>
<tr><td>Loans over 90 days past due, accruing</td><td></td><td>-</td><td></td><td>18</td><td></td><td>-</td><td></td><td>151</td><td></td><td>-</td></tr>
<tr><td>Special mention loans</td><td></td><td>20,344</td><td></td><td>20,498</td><td></td><td>15,635</td><td></td><td>15,711</td><td></td><td>14,055</td></tr>
<tr><td>Substandard loans, accruing</td><td></td><td>432</td><td></td><td>455</td><td></td><td>1,125</td><td></td><td>1,150</td><td></td><td>1,463</td></tr>
<tr><td>Non performing assets/total assets</td><td></td><td>0.03 %</td><td></td><td>0.00 %</td><td></td><td>0.24 %</td><td></td><td>0.23 %</td><td></td><td>0.25 %</td></tr>
<tr><td>Past due loans/total loans</td><td></td><td>0.23 %</td><td></td><td>0.09 %</td><td></td><td>0.40 %</td><td></td><td>0.41 %</td><td></td><td>0.39 %</td></tr>
<tr><td>Capital Ratios (2)</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Total capital</td><td></td><td>$ 105,495</td><td></td><td>$ 102,888</td><td></td><td>$ 100,914</td><td></td><td>$ 99,097</td><td></td><td>$ 97,301</td></tr>
<tr><td>Tier 1 capital</td><td></td><td>97,087</td><td></td><td>94,662</td><td></td><td>92,921</td><td></td><td>91,290</td><td></td><td>89,674</td></tr>
<tr><td>Common equity tier 1 capital</td><td></td><td>97,087</td><td></td><td>94,662</td><td></td><td>92,921</td><td></td><td>91,290</td><td></td><td>89,674</td></tr>
<tr><td>Total capital to risk-weighted assets</td><td></td><td>13.98 %</td><td></td><td>13.75 %</td><td></td><td>13.74 %</td><td></td><td>13.46 %</td><td></td><td>13.61 %</td></tr>
<tr><td>Tier 1 capital to risk weighted assets</td><td></td><td>12.87 %</td><td></td><td>12.65 %</td><td></td><td>12.66 %</td><td></td><td>12.40 %</td><td></td><td>12.55 %</td></tr>
<tr><td>Common equity Tier 1 capital to risk weighed assets</td><td></td><td>12.87 %</td><td></td><td>12.65 %</td><td></td><td>12.66 %</td><td></td><td>12.40 %</td><td></td><td>12.55 %</td></tr>
<tr><td>Leverage ratio</td><td></td><td>10.02 %</td><td></td><td>9.71 %</td><td></td><td>9.84 %</td><td></td><td>9.91 %</td><td></td><td>10.06 %</td></tr>
</table>
<table>
<tr><td>POTOMAC BANCSHARES, INC.</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Non-GAAP Reconciliations</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>(in thousands, except share and per share data)</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>(unaudited)</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td></td><td></td><td>As of or for the Three Months Ended</td></tr>
<tr><td></td><td></td><td>March 31,</td><td></td><td>December 31,</td><td></td><td>September 30,</td><td></td><td>June 30,</td><td></td><td>March 31,</td></tr>
<tr><td></td><td></td><td>2026</td><td></td><td>2025</td><td></td><td>2025</td><td></td><td>2025</td><td></td><td>2025</td></tr>
<tr><td>Adjusted Net Income</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Net income (GAAP)</td><td></td><td>$ 3,044</td><td></td><td>$ 2,372</td><td></td><td>$ 2,322</td><td></td><td>$ 2,074</td><td></td><td>$ 2,188</td></tr>
<tr><td>Add: Loss on sale of securities</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td></tr>
<tr><td>Add: Core system conversion expense</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td><td></td><td>85</td><td></td><td>-</td></tr>
<tr><td>Add: Renaming expense</td><td></td><td>-</td><td></td><td>154</td><td></td><td>82</td><td></td><td>22</td><td></td><td>-</td></tr>
<tr><td>Subtract: Interest income recognized on nonaccrual loans from prior periods</td><td></td><td>-</td><td></td><td>(405)</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td></tr>
<tr><td>Subtract: BOLI death benefit</td><td></td><td>(227)</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td></tr>
<tr><td>Total adjustments</td><td></td><td>$ (227)</td><td></td><td>$ (251)</td><td></td><td>$ 82</td><td></td><td>$ 107</td><td></td><td>$ -</td></tr>
<tr><td>Subtract: Tax effect of adjustment (4)</td><td></td><td>48</td><td></td><td>53</td><td></td><td>(17)</td><td></td><td>(22)</td><td></td><td>-</td></tr>
<tr><td>Adjusted net income (non-GAAP)</td><td></td><td>$ 2,865</td><td></td><td>$ 2,174</td><td></td><td>$ 2,387</td><td></td><td>$ 2,159</td><td></td><td>$ 2,188</td></tr>
<tr><td>Adjusted Earnings Per Share, Basic</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Weighted average shares, basic</td><td></td><td>4,144,561</td><td></td><td>4,144,561</td><td></td><td>4,144,561</td><td></td><td>4,144,561</td><td></td><td>4,144,561</td></tr>
<tr><td>Basic earnings per share (GAAP)</td><td></td><td>$ 0.73</td><td></td><td>$ 0.57</td><td></td><td>$ 0.56</td><td></td><td>$ 0.50</td><td></td><td>$ 0.53</td></tr>
<tr><td>Adjusted earnings per share, basic (Non-GAAP)</td><td></td><td>$ 0.69</td><td></td><td>$ 0.52</td><td></td><td>$ 0.58</td><td></td><td>$ 0.52</td><td></td><td>$ 0.53</td></tr>
<tr><td>Adjusted Earnings Per Share, Diluted</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Weighted average shares, diluted</td><td></td><td>4,144,561</td><td></td><td>4,144,561</td><td></td><td>4,144,561</td><td></td><td>4,144,561</td><td></td><td>4,144,561</td></tr>
<tr><td>Diluted earnings per share (GAAP)</td><td></td><td>$ 0.73</td><td></td><td>$ 0.57</td><td></td><td>$ 0.56</td><td></td><td>$ 0.50</td><td></td><td>$ 0.53</td></tr>
<tr><td>Adjusted earnings per share, diluted (Non-GAAP)</td><td></td><td>$ 0.69</td><td></td><td>$ 0.52</td><td></td><td>$ 0.58</td><td></td><td>$ 0.52</td><td></td><td>$ 0.53</td></tr>
<tr><td>Adjusted Pre-Provision, Pre-tax earnings</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Net interest income</td><td></td><td>$ 8,393</td><td></td><td>$ 8,784</td><td></td><td>$ 8,064</td><td></td><td>$ 7,634</td><td></td><td>$ 7,354</td></tr>
<tr><td>Total noninterest income</td><td></td><td>2,840</td><td></td><td>2,046</td><td></td><td>1,914</td><td></td><td>1,766</td><td></td><td>1,829</td></tr>
<tr><td>Net revenue</td><td></td><td>$ 11,233</td><td></td><td>$ 10,830</td><td></td><td>$ 9,978</td><td></td><td>$ 9,400</td><td></td><td>$ 9,183</td></tr>
<tr><td>Total noninterest expense</td><td></td><td>7,146</td><td></td><td>7,493</td><td></td><td>6,785</td><td></td><td>6,499</td><td></td><td>6,201</td></tr>
<tr><td>Pre-provision, pre-tax earnings</td><td></td><td>$ 4,087</td><td></td><td>$ 3,337</td><td></td><td>$ 3,193</td><td></td><td>$ 2,901</td><td></td><td>$ 2,982</td></tr>
<tr><td>Add: Loss on sale of securities</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td></tr>
<tr><td>Add: Core system conversion expense</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td><td></td><td>85</td><td></td><td>-</td></tr>
<tr><td>Add: Bank renaming expense</td><td></td><td>-</td><td></td><td>154</td><td></td><td>82</td><td></td><td>22</td><td></td><td>-</td></tr>
<tr><td>Subtract: Interest income recognized on nonaccrual loans from prior periods</td><td></td><td>-</td><td></td><td>(405)</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td></tr>
<tr><td>Subtract: BOLI death benefit</td><td></td><td>(227)</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td></tr>
<tr><td>Adjusted pre-provision, pre-tax earnings</td><td></td><td>$ 3,860</td><td></td><td>$ 3,086</td><td></td><td>$ 3,275</td><td></td><td>$ 3,008</td><td></td><td>$ 2,982</td></tr>
<tr><td>Adjusted Performance Ratios</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Average assets</td><td></td><td>$ 961,992</td><td></td><td>$ 968,056</td><td></td><td>$ 936,572</td><td></td><td>$ 912,253</td><td></td><td>$ 881,490</td></tr>
<tr><td>Return on average assets (GAAP)</td><td></td><td>1.28 %</td><td></td><td>0.97 %</td><td></td><td>0.98 %</td><td></td><td>0.91 %</td><td></td><td>1.01 %</td></tr>
<tr><td>Adjusted return on average assets (Non-GAAP)</td><td></td><td>1.21 %</td><td></td><td>0.89 %</td><td></td><td>1.01 %</td><td></td><td>0.95 %</td><td></td><td>1.01 %</td></tr>
<tr><td>Average shareholders&#x27; equity</td><td></td><td>$ 84,077</td><td></td><td>$ 81,783</td><td></td><td>$ 79,290</td><td></td><td>$ 76,808</td><td></td><td>$ 74,694</td></tr>
<tr><td>Return on average equity (GAAP)</td><td></td><td>14.68 %</td><td></td><td>11.51 %</td><td></td><td>11.62 %</td><td></td><td>10.83 %</td><td></td><td>11.88 %</td></tr>
<tr><td>Adjusted return on average equity (Non-GAAP)</td><td></td><td>13.82 %</td><td></td><td>10.55 %</td><td></td><td>11.94 %</td><td></td><td>11.27 %</td><td></td><td>11.88 %</td></tr>
<tr><td>Pre-provision, pre-tax return on average assets</td><td></td><td>1.72 %</td><td></td><td>1.37 %</td><td></td><td>1.35 %</td><td></td><td>1.28 %</td><td></td><td>1.37 %</td></tr>
<tr><td>Adjusted pre-provision, pre-tax return on average assets</td><td></td><td>1.63 %</td><td></td><td>1.26 %</td><td></td><td>1.39 %</td><td></td><td>1.32 %</td><td></td><td>1.37 %</td></tr>
<tr><td>Net Interest Margin</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Tax-equivalent net interest income</td><td></td><td>$ 8,399</td><td></td><td>$ 8,385</td><td></td><td>$ 8,070</td><td></td><td>$ 7,640</td><td></td><td>$ 7,360</td></tr>
<tr><td>Average earning assets</td><td></td><td>930,543</td><td></td><td>937,335</td><td></td><td>905,307</td><td></td><td>881,485</td><td></td><td>850,035</td></tr>
<tr><td>Net interest margin</td><td></td><td>3.66 %</td><td></td><td>3.55 %</td><td></td><td>3.54 %</td><td></td><td>3.48 %</td><td></td><td>3.51 %</td></tr>
<tr><td>Efficiency Ratio</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Total noninterest expense</td><td></td><td>$ 7,146</td><td></td><td>$ 7,493</td><td></td><td>$ 6,785</td><td></td><td>$ 6,499</td><td></td><td>$ 6,201</td></tr>
<tr><td>Subtract: Core system conversion expense</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td><td></td><td>(85)</td><td></td><td>-</td></tr>
<tr><td>Subtract: Renaming expense</td><td></td><td>-</td><td></td><td>(154)</td><td></td><td>(82)</td><td></td><td>(22)</td><td></td><td>-</td></tr>
<tr><td>Total noninterest expense subtotal</td><td></td><td>$ 7,146</td><td></td><td>$ 7,339</td><td></td><td>$ 6,703</td><td></td><td>$ 6,392</td><td></td><td>$ 6,201</td></tr>
<tr><td>Tax-equivalent net interest income</td><td></td><td>$ 8,399</td><td></td><td>$ 8,385</td><td></td><td>$ 8,070</td><td></td><td>$ 7,640</td><td></td><td>$ 7,360</td></tr>
<tr><td>Total noninterest income</td><td></td><td>$ 2,840</td><td></td><td>$ 2,046</td><td></td><td>$ 1,914</td><td></td><td>$ 1,766</td><td></td><td>$ 1,829</td></tr>
<tr><td>Add: Net losses on disposal of premises &amp; equipment</td><td></td><td>9</td><td></td><td>10</td><td></td><td>1</td><td></td><td>-</td><td></td><td>2</td></tr>
<tr><td>Subtract: Bank owned life insurance death benefit</td><td></td><td>(227)</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td></tr>
<tr><td>Total noninterest income subtotal</td><td></td><td>$ 2,622</td><td></td><td>$ 2,056</td><td></td><td>$ 1,915</td><td></td><td>$ 1,766</td><td></td><td>$ 1,831</td></tr>
<tr><td>Subtotal</td><td></td><td>$ 11,021</td><td></td><td>$ 10,441</td><td></td><td>$ 9,985</td><td></td><td>$ 9,406</td><td></td><td>$ 9,191</td></tr>
<tr><td>Efficiency ratio</td><td></td><td>64.84 %</td><td></td><td>70.29 %</td><td></td><td>67.13 %</td><td></td><td>67.96 %</td><td></td><td>67.47 %</td></tr>
<tr><td>Tax-Equivalent Net Interest Income</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>GAAP measures:</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Interest income - loans</td><td></td><td>$ 10,485</td><td></td><td>$ 10,727</td><td></td><td>$ 10,447</td><td></td><td>$ 9,682</td><td></td><td>$ 9,501</td></tr>
<tr><td>Interest income - investments taxable</td><td></td><td>796</td><td></td><td>732</td><td></td><td>709</td><td></td><td>710</td><td></td><td>715</td></tr>
<tr><td>Interest income - investments tax exempt</td><td></td><td>29</td><td></td><td>29</td><td></td><td>30</td><td></td><td>28</td><td></td><td>29</td></tr>
<tr><td>Interest income - other</td><td></td><td>833</td><td></td><td>1,285</td><td></td><td>1,060</td><td></td><td>989</td><td></td><td>674</td></tr>
<tr><td>Interest expense - deposits</td><td></td><td>(3,243)</td><td></td><td>(3,445)</td><td></td><td>(3,709)</td><td></td><td>(3,324)</td><td></td><td>(3,105)</td></tr>
<tr><td>Interest expense - short term borrowings</td><td></td><td>(3)</td><td></td><td>(8)</td><td></td><td>(9)</td><td></td><td>(2)</td><td></td><td>(6)</td></tr>
<tr><td>Interest expense - long term borrowings</td><td></td><td>(290)</td><td></td><td>(312)</td><td></td><td>(312)</td><td></td><td>(309)</td><td></td><td>(313)</td></tr>
<tr><td>Interest expense - subordinated debt</td><td></td><td>(214)</td><td></td><td>(224)</td><td></td><td>(152)</td><td></td><td>(140)</td><td></td><td>(141)</td></tr>
<tr><td>Net interest income</td><td></td><td>$ 8,393</td><td></td><td>$ 8,784</td><td></td><td>$ 8,064</td><td></td><td>$ 7,634</td><td></td><td>$ 7,354</td></tr>
<tr><td>Non-GAAP measures:</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Subtract: Interest income recognized on non-accrual loans from prior periods</td><td></td><td>-</td><td></td><td>(405)</td><td></td><td>-</td><td></td><td>-</td><td></td><td>-</td></tr>
<tr><td>Add: Tax benefit realized on non-taxable interest income - municipal securities (4)</td><td></td><td>$ 6</td><td></td><td>$ 6</td><td></td><td>$ 6</td><td></td><td>$ 6</td><td></td><td>$ 6</td></tr>
<tr><td>Tax equivalent net interest income</td><td></td><td>$ 8,399</td><td></td><td>$ 8,385</td><td></td><td>$ 8,070</td><td></td><td>$ 7,640</td><td></td><td>$ 7,360</td></tr>
<tr><td>Tangible Book Value Per Share</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Tangible common equity</td><td></td><td>$ 84,637</td><td></td><td>$ 82,443</td><td></td><td>$ 80,359</td><td></td><td>$ 77,490</td><td></td><td>$ 76,049</td></tr>
<tr><td>Common shares outstanding, ending</td><td></td><td>4,144,561</td><td></td><td>4,144,561</td><td></td><td>4,144,561</td><td></td><td>4,144,561</td><td></td><td>4,144,561</td></tr>
<tr><td>Tangible book value per share</td><td></td><td>$ 20.42</td><td></td><td>$ 19.89</td><td></td><td>$ 19.39</td><td></td><td>$ 18.70</td><td></td><td>$ 18.35</td></tr>
<tr><td>(1) Non-GAAP financial measures. See &quot;Non-GAAP Financial Measures&quot; and &quot;Non-GAAP Reconciliations&quot; for additional information and detailed calculations of adjustments.</td></tr>
<tr><td>(2) Capital ratios are for Potomac Bank.</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>(3) Capital ratios are for Potomac Bancshares, Inc.</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>(4) The tax rate utilized in calculating the tax benefit is 21%</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>(5) Nonperforming assets are comprised of nonaccrual loans. There was no other real estate owned for the periods presented.</td><td></td><td></td></tr>
</table>
</body></html>
'''

# https://www.prnewswire.com/news-releases/touchmark-bancshares-inc-reports-first-quarter-results-302764593.html
TMAK_1Q26 = '''
<html><body>

<table>
<tr><td>TOUCHMARK BANCSHARES, INC. AND SUBSIDIARY CONSOLIDATED BALANCE SHEETS</td></tr>
<tr><td></td><td></td><td></td><td>(unaudited)</td><td></td><td></td></tr>
<tr><td></td><td></td><td></td><td>March 31,</td><td></td><td>December 31,</td></tr>
<tr><td></td><td>(dollars in thousands, except per share data)</td><td></td><td>2026</td><td></td><td>2025 (1)</td></tr>
<tr><td>ASSETS</td><td>Cash and due from banks</td><td></td><td>$</td><td>444</td><td></td><td>$</td><td>607</td></tr>
<tr><td></td><td>Interest-bearing deposits</td><td></td><td>61,404</td><td></td><td>65,041</td></tr>
<tr><td></td><td>Federal funds sold</td><td></td><td>5,175</td><td></td><td>5,175</td></tr>
<tr><td></td><td>Total cash and cash equivalents</td><td></td><td>67,023</td><td></td><td>70,823</td></tr>
<tr><td></td><td>Available-for-sale securities</td><td></td><td>8,367</td><td></td><td>10,806</td></tr>
<tr><td></td><td>Equity securities</td><td></td><td>1,577</td><td></td><td>1,598</td></tr>
<tr><td></td><td>Loans, net of deferred fees and purchased premiums</td><td></td><td>320,708</td><td></td><td>324,725</td></tr>
<tr><td></td><td>Allowance for credit losses</td><td></td><td>(2,436)</td><td></td><td>(2,543)</td></tr>
<tr><td></td><td>Net loans</td><td></td><td>318,272</td><td></td><td>322,182</td></tr>
<tr><td></td><td>Bank premises and equipment, net</td><td></td><td>1,450</td><td></td><td>1,490</td></tr>
<tr><td></td><td>Other Real Estate</td><td></td><td>5,826</td><td></td><td>5,826</td></tr>
<tr><td></td><td>Deferred tax asset</td><td></td><td>1,370</td><td></td><td>1,351</td></tr>
<tr><td></td><td>Other assets</td><td></td><td>6,316</td><td></td><td>3,561</td></tr>
<tr><td></td><td>TOTAL ASSETS</td><td></td><td>$</td><td>410,201</td><td></td><td>$</td><td>417,637</td></tr>
<tr><td>LIABILITIES</td><td>Deposits:</td><td></td><td></td><td></td><td></td></tr>
<tr><td></td><td>Noninterest-bearing</td><td></td><td>$</td><td>17,579</td><td></td><td>$</td><td>17,722</td></tr>
<tr><td></td><td>Interest-bearing</td><td></td><td>315,360</td><td></td><td>320,972</td></tr>
<tr><td></td><td>Total deposits</td><td></td><td>332,939</td><td></td><td>338,694</td></tr>
<tr><td></td><td>Accounts payable and accrued liabilities</td><td></td><td>6,309</td><td></td><td>8,027</td></tr>
<tr><td></td><td>TOTAL LIABILITIES</td><td></td><td>339,248</td><td></td><td>346,721</td></tr>
<tr><td>SHAREHOLDERS&#x27;</td><td>Common stock - $0.01 par value per share, 50,000,000 shares</td><td></td><td></td><td></td><td></td></tr>
<tr><td>EQUITY</td><td>authorized; 4,476,890 shares issued and outstanding as of</td><td></td><td></td><td></td><td></td></tr>
<tr><td></td><td>the periods presented</td><td></td><td>45</td><td></td><td>45</td></tr>
<tr><td></td><td>Additional paid-in capital</td><td></td><td>46,895</td><td></td><td>46,895</td></tr>
<tr><td></td><td>Retained earnings</td><td></td><td>24,617</td><td></td><td>24,523</td></tr>
<tr><td></td><td>Accumulated other comprehensive loss</td><td></td><td>(604)</td><td></td><td>(547)</td></tr>
<tr><td></td><td>TOTAL SHAREHOLDERS&#x27; EQUITY</td><td></td><td>70,953</td><td></td><td>70,916</td></tr>
<tr><td></td><td>TOTAL LIABILITIES AND SHAREHOLDERS&#x27; EQUITY</td><td></td><td>$</td><td>410,201</td><td></td><td>$</td><td>417,637</td></tr>
</table>
<table>
<tr><td></td><td>TOUCHMARK BANCSHARES, INC. AND SUBSIDIARY CONSOLIDATED STATEMENTS OF INCOME (unaudited)</td></tr>
<tr><td></td><td></td><td>Three Months Ended March 31,</td></tr>
<tr><td></td><td>(dollars in thousands, except per share data)</td><td>2026</td><td></td><td>2025</td></tr>
<tr><td>INTEREST AND</td><td>Interest and fees on loans</td><td>$</td><td>4,209</td><td></td><td>$</td><td>6,202</td></tr>
<tr><td>DIVIDEND</td><td>Income on investment securities</td><td></td><td></td><td></td></tr>
<tr><td>INCOME</td><td>Taxable interest</td><td>102</td><td></td><td>103</td></tr>
<tr><td></td><td>Interest from federal funds sold and other</td><td>560</td><td></td><td>488</td></tr>
<tr><td></td><td>Total interest income</td><td>4,871</td><td></td><td>6,793</td></tr>
<tr><td>INTEREST</td><td>Interest on deposits</td><td>2,808</td><td></td><td>3,855</td></tr>
<tr><td>EXPENSE</td><td>Interest on borrowings</td><td>-</td><td></td><td>-</td></tr>
<tr><td></td><td>Total interest expense</td><td>2,808</td><td></td><td>3,855</td></tr>
<tr><td></td><td>Net interest income</td><td>2,063</td><td></td><td>2,938</td></tr>
<tr><td></td><td>Provision for credit losses</td><td>150</td><td></td><td>295</td></tr>
<tr><td></td><td>Net interest income after provision</td><td>1,913</td><td></td><td>2,643</td></tr>
<tr><td>NONINTEREST</td><td></td><td></td><td></td><td></td></tr>
<tr><td>INCOME</td><td>Service fees on deposit accounts</td><td>2</td><td></td><td>3</td></tr>
<tr><td></td><td>Loan servicing fees</td><td>103</td><td></td><td>110</td></tr>
<tr><td></td><td>Other noninterest income</td><td>10</td><td></td><td>49</td></tr>
<tr><td></td><td>Total noninterest income</td><td>115</td><td></td><td>162</td></tr>
<tr><td>NONINTEREST</td><td>Salaries and employee benefits</td><td>984</td><td></td><td>983</td></tr>
<tr><td>EXPENSE</td><td>Net occupancy expense</td><td>70</td><td></td><td>67</td></tr>
<tr><td></td><td>Foreclosed real estate expenses</td><td>171</td><td></td><td>53</td></tr>
<tr><td></td><td>Data processing expense</td><td>98</td><td></td><td>94</td></tr>
<tr><td></td><td>Loan collection expense</td><td>51</td><td></td><td>5</td></tr>
<tr><td></td><td>Audits and exams expense</td><td>45</td><td></td><td>45</td></tr>
<tr><td></td><td>Board expenses</td><td>109</td><td></td><td>173</td></tr>
<tr><td></td><td>Supervisory assessments</td><td>92</td><td></td><td>93</td></tr>
<tr><td></td><td>Other noninterest expense</td><td>272</td><td></td><td>323</td></tr>
<tr><td></td><td>Total noninterest expense</td><td>1,892</td><td></td><td>1,836</td></tr>
<tr><td></td><td>Income before provision for income taxes</td><td>136</td><td></td><td>969</td></tr>
<tr><td></td><td>Provision for income taxes</td><td>42</td><td></td><td>236</td></tr>
<tr><td></td><td>Net income</td><td>$</td><td>94</td><td></td><td>$</td><td>733</td></tr>
<tr><td></td><td>Weighted average shares outstanding - basic</td><td>4,476,891</td><td></td><td>4,475,891</td></tr>
<tr><td></td><td>Weighted average shares outstanding - diluted</td><td>4,583,737</td><td></td><td>4,583,737</td></tr>
<tr><td></td><td>Earnings per share</td><td>$</td><td>0.02</td><td></td><td>$</td><td>0.16</td></tr>
<tr><td></td><td>Diluted earnings per share</td><td>$</td><td>0.02</td><td></td><td>$</td><td>0.16</td></tr>
</table>
<table>
<tr><td>TOUCHMARK BANCSHARES, INC. AND SUBSIDIARY CONSOLIDATED FINANCIAL HIGHLIGHTS (unaudited)</td></tr>
<tr><td></td><td>For the Three Months Ended</td></tr>
<tr><td>(dollars in thousands, except per share data)</td><td>March 31, 2026</td><td></td><td>December 31, 2025</td><td></td><td>September 30, 2025</td><td></td><td>June 30, 2025</td><td></td><td>March 31, 2025</td></tr>
<tr><td>Results of Operations:</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Interest income</td><td>$</td><td>4,871</td><td></td><td>$</td><td>5,473</td><td></td><td>$</td><td>6,068</td><td></td><td>$</td><td>5,415</td><td></td><td>$</td><td>6,793</td></tr>
<tr><td>Interest expense</td><td>2,808</td><td></td><td>3,090</td><td></td><td>3,374</td><td></td><td>3,507</td><td></td><td>3,855</td></tr>
<tr><td>Net interest income</td><td>2,063</td><td></td><td>2,383</td><td></td><td>2,694</td><td></td><td>1,908</td><td></td><td>2,938</td></tr>
<tr><td>Provision for credit losses</td><td>150</td><td></td><td>150</td><td></td><td>150</td><td></td><td>150</td><td></td><td>295</td></tr>
<tr><td>Non-interest income</td><td>115</td><td></td><td>152</td><td></td><td>110</td><td></td><td>604</td><td></td><td>162</td></tr>
<tr><td>Non-interest expense</td><td>1,892</td><td></td><td>1,942</td><td></td><td>1,840</td><td></td><td>1,851</td><td></td><td>1,836</td></tr>
<tr><td>Income (loss) before income taxes</td><td>136</td><td></td><td>443</td><td></td><td>814</td><td></td><td>511</td><td></td><td>969</td></tr>
<tr><td>Income taxes (benefit)</td><td>42</td><td></td><td>4</td><td></td><td>205</td><td></td><td>141</td><td></td><td>236</td></tr>
<tr><td>Net income (loss)</td><td>$</td><td>94</td><td></td><td>$</td><td>439</td><td></td><td>$</td><td>609</td><td></td><td>$</td><td>370</td><td></td><td>$</td><td>733</td></tr>
<tr><td>Per Share Data:</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Basic earnings per share</td><td>$</td><td>0.02</td><td></td><td>$</td><td>0.10</td><td></td><td>$</td><td>0.14</td><td></td><td>$</td><td>0.08</td><td></td><td>$</td><td>0.16</td></tr>
<tr><td>Diluted earnings per share</td><td>$</td><td>0.02</td><td></td><td>$</td><td>0.10</td><td></td><td>$</td><td>0.13</td><td></td><td>$</td><td>0.08</td><td></td><td>$</td><td>0.16</td></tr>
<tr><td>Book value per share</td><td>$</td><td>15.85</td><td></td><td>$</td><td>15.84</td><td></td><td>$</td><td>16.39</td><td></td><td>$</td><td>16.22</td><td></td><td>$</td><td>16.14</td></tr>
<tr><td>Weighted average shares outstanding per quarter - basic</td><td>4,476,891</td><td></td><td>4,476,630</td><td></td><td>4,475,892</td><td></td><td>4,475,891</td><td></td><td>4,475,891</td></tr>
<tr><td>Weighted average shares outstanding per quarter - diluted</td><td>4,583,737</td><td></td><td>4,583,070</td><td></td><td>4,583,737</td><td></td><td>4,583,737</td><td></td><td>4,583,737</td></tr>
<tr><td>Financial Condition Data and Ratios:</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Loans, net of deferred fees</td><td>$</td><td>320,708</td><td></td><td>$</td><td>324,725</td><td></td><td>$</td><td>329,437</td><td></td><td>$</td><td>332,335</td><td></td><td>$</td><td>362,836</td></tr>
<tr><td>Allowance for credit losses</td><td>$</td><td>(2,436)</td><td></td><td>$</td><td>(2,543)</td><td></td><td>$</td><td>(2,398)</td><td></td><td>$</td><td>(2,249)</td><td></td><td>$</td><td>(2,092)</td></tr>
<tr><td>Total assets</td><td>$</td><td>410,201</td><td></td><td>$</td><td>418,375</td><td></td><td>$</td><td>417,756</td><td></td><td>$</td><td>426,007</td><td></td><td>$</td><td>432,421</td></tr>
<tr><td>Total deposits</td><td>$</td><td>332,939</td><td></td><td>$</td><td>338,694</td><td></td><td>$</td><td>339,032</td><td></td><td>$</td><td>348,064</td><td></td><td>$</td><td>354,099</td></tr>
<tr><td>Net interest margin</td><td>1.94 %</td><td></td><td>2.32 %</td><td></td><td>2.58 %</td><td></td><td>1.83 %</td><td></td><td>2.71 %</td></tr>
<tr><td>Efficiency</td><td>85.05 %</td><td></td><td>75.08 %</td><td></td><td>64.26 %</td><td></td><td>70.65 %</td><td></td><td>58.68 %</td></tr>
<tr><td>Asset Quality Data and Ratios:</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>Total nonperforming assets</td><td>$</td><td>22,590</td><td></td><td>$</td><td>25,080</td><td></td><td>$</td><td>22,323</td><td></td><td>$</td><td>22,409</td><td></td><td>$</td><td>23,042</td></tr>
<tr><td>Total nonperforming assets, net of government guarantees</td><td>6,483</td><td></td><td>6,521</td><td></td><td>6,478</td><td></td><td>7,422</td><td></td><td>7,553</td></tr>
<tr><td>Nonperforming assets to total assets</td><td>5.51 %</td><td></td><td>5.99 %</td><td></td><td>5.34 %</td><td></td><td>5.26 %</td><td></td><td>5.33 %</td></tr>
<tr><td>Nonperforming assets to total assets, net of government guarantees</td><td>1.58 %</td><td></td><td>1.56 %</td><td></td><td>1.55 %</td><td></td><td>1.74 %</td><td></td><td>1.75 %</td></tr>
<tr><td>Allowance for credit losses to total loans</td><td>0.76 %</td><td></td><td>0.78 %</td><td></td><td>0.73 %</td><td></td><td>0.68 %</td><td></td><td>0.58 %</td></tr>
<tr><td>Net (recoveries) charge-offs to average loans (annualized)</td><td>0.32 %</td><td></td><td>0.01 %</td><td></td><td>(0.00 %)</td><td></td><td>(0.01 %)</td><td></td><td>0.60 %</td></tr>
</table>
</body></html>
'''
