# User Input History

All user messages from the Google Scholar Citation Crawler development conversation.

## Message 1 [2026-03-08T08:11:11.247Z]

[Request interrupted by user for tool use]

---

## Message 2 [2026-03-08T08:11:11.245Z]

Implement the following plan:

# Plan: Reorganize Google Scholar Citation Crawler for GitHub Release

## Context

The user has two working scripts (`fetch_author_profile.py` and `fetch_paper_citations.py`) for crawling Google Scholar author data. They want to:
1. Create a clean new project directory `google-scholar-citation-crawler`
2. Merge both steps into a unified workflow with a single entry point
3. Clean all personal/hardcoded info for public GitHub release
4. Keep a personal example script for daily use
5. All text in English (README, comments, console output)

## Message 3 [2026-03-08T08:21:26.013Z]

我不喜欢分步骤运行，profile这个step的代价并不大，就让它每次执行都会运行即可，如果引用总数、论文总数保持不变，那就什么都不用做。因此，也就可以去掉step这个参数，这样整体更简洁一些。注意更新相关文档。

---

## Message 4 [2026-03-08T08:43:41.994Z]

程序有问题，多次发生无响应超过120秒，但程序仍在执行。我的要求是，出现这样的情况就停止整个程序执行。另外，帮我统一一下所有的主动等待，时间都是30-60秒之间的随机值。不要区分base、page之类的。另外，非主动等待的heart beat间隔可以设成10秒，结束程序的时间限制可以设成80秒。

---

## Message 5 [2026-03-08T08:49:16.638Z]

我觉得主动等待和等待响应的逻辑还需要处理一下，如果是因为防止被封而进行的主动等待，不应该记在已等待时间可能遇到限流里。因此，等待时间防止超时的技时，应该在主动等待结束之后才开始，进入主动等待之后应该要清零。另外，记得每次主动等待都要输出相关信息。

---

## Message 6 [2026-03-08T08:51:44.994Z]

把这次的更新也加入到WORK_NOTES.md文档中，注意这个文档不要用git同步。

---

## Message 7 [2026-03-08T08:59:28.001Z]

skip fetching 的过程有问题。即使用户的citation总数和publication没变，还需要检查一下是否对应的citation都已经本获取了，如果没有，就继续尝试进行获取。

---

## Message 8 [2026-03-08T09:01:29.655Z]

把这个更新也加到 WORK_NOTES.md。创建git项目，从现在开始的每次修改都要进行提交。

---

## Message 9 [2026-03-08T09:03:17.126Z]

检查一下第一次主动等待的时间，好像第一次获取author信息之前不需要等待。

---

## Message 10 [2026-03-08T09:08:09.390Z]

现在用来测试的用户，第一篇论文已经爬了990个引用，现有程序在尝试继续抓取后面的引用的时候会被组织，你能不能解决一下这个问题？

---

## Message 11 [2026-03-08T09:15:14.455Z]

不能让代码连续发送99页的访问请求，我记得以前有一个monkey patch解决了这个问题，你再检查一下呢。如果不能跳页，那也需要大幅提升等待时间才行，建议统一按照这个项目30-60秒的时间进行设置。

---

## Message 12 [2026-03-08T09:23:45.885Z]

请测试一下这些主要功能，确保他们没有问题。如果当前的代理有问题，告诉我，我会换个代理再进行尝试。

---

## Message 13 [2026-03-08T09:54:36.333Z]

我已更换ip请再试一次

---

## Message 14 [2026-03-08T10:00:31.327Z]

[Request interrupted by user for tool use]

---

## Message 15 [2026-03-08T10:00:45.689Z]

好像resume会导致被封，不知道这是什么原因导致的。另外请注意，当发现重试的时候，可能已经被封了，我们不是需要在发生错误之后重试前等待，而是在每个网页访问动作之前都等待。第三，我发现现在retry wait的waiting时间不再累计了，那retry累计一个总次数即可，80秒的超时时间好像没有什么意义了。

---

## Message 16 [2026-03-08T10:49:09.177Z]

如果代理质量有问题，为什么一开始可以访问，而10次之后会被封？

---

## Message 17 [2026-03-08T10:53:20.453Z]

我发现每天论文的引用json里的数据有点问题，num_citation_cached记录的是所有的引用数，而不是当前已经缓存的引用数。

---

## Message 18 [2026-03-08T11:25:48.846Z]

为什么仍然看到这样的日志，重试这么多次应该停止了。Pagination (page 3)
      Waiting 50s before request...
      Waiting 49s before request...
      Waiting 36s before request...
      Waiting 53s before request...
      Waiting 57s before request...
      Waiting 54s before request...
      Waiting 53s before request...
      Waiting 40s before request...

---

## Message 19 [2026-03-08T11:29:36.600Z]

This session is being continued from a previous conversation that ran out of context. The summary below covers the earlier portion of the conversation.


---

## Message 20 [2026-03-08T11:31:07.034Z]

发现403就两次就不要再重试了。

---

## Message 21 [2026-03-08T11:40:09.898Z]

这个脚本中的重试次数是怎么设的？也少一点

---

## Message 22 [2026-03-08T11:52:28.429Z]

我们外层的重试改为隔3个小时进行。

---

## Message 23 [2026-03-08T11:53:22.939Z]

注意不是30-60s的时间改成3小时，而是发现无法连接之后，隔3小时再试。

---

## Message 24 [2026-03-08T11:56:30.851Z]

重试次数用完，保存已有进度，再等待6小时。再继续，如果还是失败，就打印当前时间并终止尝试。

---

## Message 25 [2026-03-08T11:58:09.290Z]

另外做个计数，记录一下从开始执行一共进行了多少次页面访问。在出错的时候输出一下这个技数。

---

## Message 26 [2026-03-08T12:01:09.186Z]

git提交，更新readme，记录work progress

---

## Message 27 [2026-03-08T12:21:56.256Z]

我刚刚更新了代理，请测试一下990和360这两个已经获取部分引用的论文继续获取的情况。

---

## Message 28 [2026-03-08T12:25:52.101Z]

[Request interrupted by user for tool use]

---

## Message 29 [2026-03-08T12:25:52.102Z]

我看这里又已经被ban了，你赶快想想办法。

---

## Message 30 [2026-03-08T12:31:42.480Z]

[Request interrupted by user for tool use]

---

## Message 31 [2026-03-08T12:31:42.496Z]

我们可以考虑先按照年份查询，然后再使用skip或者start的机制。

---

## Message 32 [2026-03-08T12:36:25.530Z]

[Request interrupted by user for tool use]

---

## Message 33 [2026-03-08T12:43:32.531Z]

现在可以开始测试了

---

## Message 34 [2026-03-08T12:50:13.689Z]

[Request interrupted by user for tool use]

---

## Message 35 [2026-03-08T12:50:45.136Z]

你看从一开始就不能获取，这该怎么办？我再换个代理，我们再试一次。开始吧。

---

## Message 36 [2026-03-08T12:51:52.669Z]

[Request interrupted by user for tool use]

---

## Message 37 [2026-03-08T13:02:41.555Z]

从一开始就不能获取，这该怎么办？我再换个代理，我们再试一次。开始吧。

---

## Message 38 [2026-03-08T14:52:36.007Z]

暂时中断吧，我再观察观察

---

## Message 39 [2026-03-08T14:54:14.107Z]

我感觉这个过程中好像session refresh没起到什么作用？

---

## Message 40 [2026-03-08T14:57:49.360Z]

改为每10-20页中第一个随机值来刷新session吧。

---

## Message 41 [2026-03-08T15:02:52.012Z]

但在切换论文等类似的地方重新刷新一次。

---

## Message 42 [2026-03-08T23:42:53.054Z]

对不是对于大于一定数量的引用，first fetch的时候就按照时间会比较科学？

---

## Message 43 [2026-03-09T00:28:07.130Z]

git提交，更新work notes

---

## Message 44 [2026-03-09T08:36:13.862Z]

在retry的时候都加上时间输出，这样能知道是什么时候发生的。另外在脚本里做一个技数，看一下本次运行抓取了多少个citation，在写入日志的时候输出。

---

## Message 45 [2026-03-09T09:05:46.262Z]

检查一下缓存保存的机制：1. 每篇论文的全部引用抓取完成后都要写一次缓存。2. 即使用户中断了当前抓取过程，或者因为其他原因停止，也要把已经抓取到的内容写到最终的结果文件里。 此外，确认一下每次waiting的时候都输出当前等待的位置，比如是新打开的页面或者是在翻页等等。在waiting的时候输出一些全局性的信息，比如累计运行了多长时间，抓取了多少新的citation等。

---

## Message 46 [2026-03-09T13:14:49.889Z]

我已经完成了一次全部页面的抓取。但是我发现总引用数量增加之后，好像程序里并没有捕获到这样的变化。你能帮我测试一下吗？对比一下curl获取的结果和程序运行的结果。

---


## Message 47 [2026-03-10T00:16:12.216Z]

这时候citation的总数对了，但是程序好像没有更新citation的本地缓存？

---

## Message 48 [2026-03-10T01:09:45.239Z]

history.json和profile.json都包含历史信息，是不是有些重复了？另外，每次运行时，建议也检查一下每篇论文的引用数和缓存数是否一致，如果不一致也需要更新。

---

## Message 49 [2026-03-10T01:58:15.226Z]

再检查一下确认xlxs和json文件写入的内容是一致的。

---

## Message 50 [2026-03-10T03:46:54.006Z]

目前就够了，但是我看现在明明citations collected和citation on scholar是不一样的，为什么程序会觉得已经更新完毕了？

---

## Message 51 [2026-03-10T04:05:03.109Z]

Multilingual machine translation with large langua... 615 -> 621  为什么这里显示citation已经621了，cache才601，却不更新？ [3/231] Multilingual machine translation with large language mo... -> cached (601 citations)

---

## Message 52 [2026-03-10T04:07:00.372Z]

不对，逻辑设成如果cache的更多就不更新，如果cache少，就一定要更新。

---

## Message 53 [2026-03-10T04:11:28.639Z]

Error: name 'need_fetch' is not defined
Traceback (most recent call last):
  File "/Users/username/test_claude/google-scholar-citation-crawler/scholar_citation.py", line 1267, in <module>
    main()
  File "/Users/username/test_claude/google-scholar-citation-crawler/scholar_citation.py", line 1260, in main
    success = citation_fetcher.run()
              ^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/username/test_claude/google-scholar-citation-crawler/scholar_citation.py", line 1049, in run
    self._run_main_loop(publications, cache_status, url_map, results, fetch_idx)
  File "/Users/username/test_claude/google-scholar-citation-crawler/scholar_citation.py", line 1075, in _run_main_loop
    (i for i, (p, _, _) in enumerate(need_fetch) if p['title'] == title), -1
                                     ^^^^^^^^^^

---

## Message 54 [2026-03-10T04:31:14.112Z]

在因为新增引用导致重新获取引用的时候，主要考虑最新一年里的引用新增情况，如果最新一年的新增引用数目加上原有引用已经超过引用总数了，就不需要再检查再以前年份的引用了。

---

## Message 55 [2026-03-10T05:26:40.084Z]

是不是考虑测试一下？如果代理不行就晚一点再测吧。

---

## Message 56 [2026-03-10T07:03:42.832Z]

可能这里确实需要一个force update的选项？比如如果更新profile之后断线了，下次再更新就会有问题。

---

## Message 57 [2026-03-10T12:51:29.858Z]

等下，目前抓取的引用都没有去重吗？请保证获取的内容是没有重复的。否则计数是不对的。

---

## Message 58 [2026-03-10T12:56:22.520Z]

更新一下 work notes，把最近的改动都记上

---

## Message 59 [2026-03-10T13:02:12.609Z]

---

## Message 60 [2026-03-10T13:03:44.420Z]

这里不太对，明明web上是1324，这里也发现不足了，怎么没开始补充？
  resume (1302 cached, fetching remaining)
  Year-based resume: 2017-2026 (10 years already done)
  Done: 1302 citations cached
  Waiting 49s before next paper... [elapsed 0s, 0 new citations, 0 pages]

---

## Message 61 [2026-03-10T13:08:13.211Z]

请在上述时候输出一下相关情况，比如expected count，cached count等等。

---

## Message 62 [2026-03-10T13:17:55.034Z]

resume (313 cached, fetching remaining)
  Year-based resume: 2019-2026 (0 years already done)
      Year 2026: resuming from position 5
      Waiting 48s before request... [elapsed 2m12s, 0 new citations, 1 pages]
      Year 2026 done: 5 citations (0 new)
      Year 2025: resuming from position 49
      Waiting 39s before request... [elapsed 3m02s, 0 new citations, 2 pages]
      Year 2025 done: 49 citations (0 new)
      Year 2024: resuming from position 62
      Waiting 57s before request... [elapsed 3m42s, 0 new citations, 3 pages]
      Year 2024 done: 62 citations (0 new)
      Year 2023: resuming from position 63
      Waiting 32s before request... [elapsed 4m41s, 0 new citations, 4 pages]
      Year 2023 done: 63 citations (0 new)
      Year 2022: resuming from position 64
      Waiting 46s before request... [elapsed 5m15s, 0 new citations, 5 pages]
      Year 2022 done: 64 citations (0 new)
      Year 2021: resuming from position 46
      Waiting 51s before request... [elapsed 6m04s, 0 new citations, 6 pages]
      Year 2021 done: 46 citations (0 new)
      Year 2020: resuming from position 15
      Waiting 48s before request... [elapsed 6m56s, 0 new citations, 7 pages]
      Year 2020 done: 15 citations (0 new)
      Year 2019: resuming from position 3
      Waiting 47s before request... [elapsed 7m46s, 0 new citations, 8 pages]
      Year 2019 done: 3 citations (0 new)
  Done: 313 citations cached
 这是为什么？

---

## Message 63 [2026-03-10T13:22:45.637Z]

resume (313 cached, fetching remaining)
  Year-based resume: 2019-2026 (0 years already done)
      Year 2026: resuming from position 5
      Waiting 48s before request... [elapsed 2m12s, 0 new citations, 1 pages]
      Year 2026 done: 5 citations (0 new)
      Year 2025: resuming from position 49
      Waiting 39s before request... [elapsed 3m02s, 0 new citations, 2 pages]
      Year 2025 done: 49 citations (0 new)
      Year 2024: resuming from position 62
      Waiting 57s before request... [elapsed 3m42s, 0 new citations, 3 pages]
      Year 2024 done: 62 citations (0 new)
      Year 2023: resuming from position 63
      Waiting 32s before request... [elapsed 4m41s, 0 new citations, 4 pages]
      Year 2023 done: 63 citations (0 new)
      Year 2022: resuming from position 64
      Waiting 46s before request... [elapsed 5m15s, 0 new citations, 5 pages]
      Year 2022 done: 64 citations (0 new)
      Year 2021: resuming from position 46
      Waiting 51s before request... [elapsed 6m04s, 0 new citations, 6 pages]
      Year 2021 done: 46 citations (0 new)
      Year 2020: resuming from position 15
      Waiting 48s before request... [elapsed 6m56s, 0 new citations, 7 pages]
      Year 2020 done: 15 citations (0 new)
      Year 2019: resuming from position 3
      Waiting 47s before request... [elapsed 7m46s, 0 new citations, 8 pages]
      Year 2019 done: 3 citations (0 new)
  Done: 313 citations cached
 这是为什么？

---

## Message 64 [2026-03-10T13:27:16.273Z]

怎么知道是漏抓了还是新增了呢？

---

## Message 65 [2026-03-10T13:30:42.282Z]

好的，确认就好，请git提交。

---

## Message 66 [2026-03-10T23:33:23.254Z]

我准备上传这个项目了，帮我再次检查一下有没有泄露隐私的内容。

---

## Message 67 [2026-03-10T23:37:17.986Z]

好的，改成占位符吧。应该不设置代理也可以正常使用这个程序吧？比如https_proxy=None ?

---

## Message 68 [2026-03-10T23:38:36.475Z]

但是这个代理存在在我的git历史记录里该怎么办？

---

## Message 69 [2026-03-11T00:10:08.583Z]

为什么这个论文，页面上的引用是1324，cache的是1302但是却没有发生更新？[1/231] Deep matrix factorization models for recommender system... -> cached (1302 citations)

---

## Message 70 [2026-03-11T00:11:46.498Z]

那个时候应该会触发一次整体的引用下载吧？

---

## Message 71 [2026-03-11T00:15:43.362Z]

建议每次从新的年份开始抓取的时候，用最新年份到当年的引用数（一般是刚刚更新过）加上更久远的引用数（一般是在cache里），如果达到引用总数就不在向前追溯。

---
关于新增数也存在问题，因为有可能之前抓取的数量不全，遇到类似这样的情况还是要
保证总数是匹配的，不能只考虑新增数量。如果我使用force
refresh能解决这个问题吗？force refresh的时候如果期望的引用数量和缓存的一致，就
不要再更新了。只要更新那些不一致的情况。

## Message 72 [2026-03-11T00:30:00.000Z]

关于新增数也存在问题，因为有可能之前抓取的数量不全，遇到类似这样的情况还是要保证总数是匹配的，不能只考虑新增数量。如果我使用force refresh能解决这个问题吗？force refresh的时候如果期望的引用数量和缓存的一致，就不要再更新了。只要更新那些不一致的情况。

---

## Message 73 [2026-03-11T00:35:00.000Z]

在这个项目中，后续每次接到命令，请先把命令写到user.md里，然后再执行。

---

## Message 74 [2026-03-11]

确认一下新加入的force命令跟之前的force命令有什么关系。

---

## Message 75 [2026-03-11]

更新一下README和work notes

---

## Message 76 [2026-03-11]

帮我检查一下work notes和user.md中间有没有泄露隐私的部分。我计划把他们也发布出去。

---

## Message 77 [2026-03-11]

好的，请帮我处理一下。

---

## Message 78 [2026-03-12]

怎么传参数可以强制重新抓取全部内容？

---

## Message 79 [2026-03-12]

我发现--force-refresh-citations也并没有更新。不要测试了，你读一读代码吧。

---

## Message 80 [2026-03-12]

为什么仍然有些scholar的citation数量大于cached citation的论文不会进入抓取流程？能不能force的时候就强制重新获取他们？

---

## Message 81 [2026-03-12]

resume (1302 cached, fetching remaining)
  Year-based resume: 2017-2026 (10 years already done)
我看到的是这样的，然后转到下一篇论文了。

---

## Message 82 [2026-03-12]

按年份获取的时候，为什么页面上有19篇引用，而我们获取的数值是18？这是哪里的问题？

---

## Message 83 [2026-03-12]

请在去重的时候输出一下重复的两个条目，方便后期检验。

---

## Message 84 [2026-03-12]

这个数字是说重新获取了2026年的引用，一共18篇吗？这时候是否查重？
（上下文：Year 2026 done: 18 citations (0 new)）

---

## Message 85 [2026-03-12]

就以这个来测试一下：Deep matrix factorization models for recommender system，2026年的引用。网页上明明是19条。现在就测试一下，直接从网页获取，或者单独调用scholarly能获取到多少条。

---

## Message 86 [2026-03-12]

天哪，当一页上就只有9个引用的时候，你写的程序就只获取了8个，这太可怕了吧。你赶快检查一下是怎么回事。

---

## Message 89 [2026-03-12]

为什么还是18个。。

---

## Message 90 [2026-03-13]

能不能帮我查一下这个参数到底是什么意思？&as_sdt=N,33

---

## Message 91 [2026-03-13]

三小时和六小时的等待时间要拉长至少一倍

---

## Message 92 [2026-03-13]

请把近期更新的内容整理记录到worknotes里

---

## Message 93 [2026-03-14]

这是又引入了什么bug？在抓取citation的时候，第一篇论文的访问里就出现了：Error (attempt 1/3, total pages: 0, new citations: 0): Client.__init__() got an unexpected keyword argument 'proxies'

---

## Message 94 [2026-03-14]

最近两次访问在fetch citation第一篇论文的时候都失败了。但是获取profile信息都还正常，请检查一下是不是什么地方弄错了？

---

## Message 95 [2026-03-14]

（运行输出）
Year-based resume: 2017-2026 (0 years already done)
      Year 2026: fetching
      Waiting 41s before request... [elapsed 0s, 0 new citations, 1 pages]
      Waiting 53s before request (retry 2)... [elapsed 47s, 0 new citations, 1 pages]
      Waiting 47s before request (retry 3)... [elapsed 4m11s, 0 new citations, 1 pages]
  [2026-03-14 09:49:31] Error (attempt 1/3, total pages: 1, ...

---

## Message 96 [2026-03-14]

我就想确认一下为什么这种url访问会被ban，有没有绕过的办法？

---

## Message 97 [2026-03-14]

可以随机化论文顺序，也可以加回浏览器特征参数，但是不应该影响取回的结果。之前去掉特征参数是为了得到跟浏览器浏览一致的结果。

---

## Message 98 [2026-03-14]

会不会还有其他的取值能正常访问？或者能不能模拟浏览器生成的访问url形式？或者改变地区代码变成别的？

---

## Message 99 [2026-03-14]

我这里有个浏览器的url记录，你看一看 https://scholar.google.com.hk/scholar?start=20&hl=en&as_sdt=2005&sciodt=0,5&as_ylo=2026&cites=5507039711609773325,3066613168129831521&scipsc=

---

## Message 100 [2026-03-14]

我不建议靠猜测，还是应该查一下as_sdt参数的含义，是不是worknotes里有记录？你也可以再查一查确认一下。

---

## Message 101 [2026-03-14]

我建议把as_sdt改成0,5 sciodt是个用于控制session的参数，现在不知道该怎么设置，那就暂时保持不变吧。你觉得呢？

---

## Message 102 [2026-03-14]

在申请访问url的时候，把url也打印出来，以便观察和比较

---

## Message 103 [2026-03-14]

当更新了部分引用内容的时候，这个输出是不对的，请更正一下:Done! 198 papers, 2464 total citation records (0 new in this run)

---

## Message 104 [2026-03-14]

我感觉即使是forced refresh了，新check过的citation和以前保存的citation也应该合并起来算总和才对。

---

## Message 105 [2026-03-14]

最近是不是没有更新user.md，请更新一下

---

## Message 106 [2026-03-15]

在对每一篇论文抓取引用的时候，能不能考虑把重复的引用数量记录一下，这样知道该篇论文的总引用数是多少，因为重复去掉的论文数量是多少。后面再次抓取的时候，不管是否force，如果单篇论文的总引用数（包含cached和去重的）跟scholar页面上的一致，就不需要再重新抓取了。

---

## Message 107 [2026-03-15]

注意我们记录的seen或者重复，是scholar的列表中本身包含重复。如果是新抓取的引用跟以前缓存的引用重复，可不能计数。否则force的时候就会有大量重复进入计数。

---

## Message 108 [2026-03-16]

为什么突然报了这个错误？Failed to fetch basic info: 'NoneType' object has no attribute 'get'
Traceback (most recent call last):
  File "scholar_citation.py", line 155, in fetch_basics
    author = scholarly.search_author_id(self.author_id)

---

## Message 109 [2026-03-17]

我给了参数是--skip 151 --limit 1，为什么没有进行抓取程序就结束了？

---

## Message 110 [2026-03-17]

另外，如果有skip参数的话，论文的抓取排序不能随便修改，否则就没办法定位到某个具体的论文了。

---

## Message 111 [2026-03-17]

--skip不是在需要抓取的论文里排序，应该是在所有论文列表里排序才对吧

---

## Message 112 [2026-03-17]

稍等，--limit的语义应该是只处理1篇论文，不要管他是否被skip。即使判断他应该被skip也算处理过了。

---

## Message 113 [2026-03-17]

文字上有些问题，--limit N是指在--skip M的数量基础上，处理N篇。实际处理的是M+1到M+N篇。我的意思是，不管这N篇在后续的状态是因为已经获取了足够的引用而被跳过，还是需要重新进行抓取，都确定是这N篇。

---

## Message 114 [2026-03-17]

我发现在有很多引用的论文引用抓取过程中，抓完了部分年份之后，如果因为网络问题发生中断，不会讲这些年份设置为done，下一次进行的时候又会从头进行抓取，请确认一下是否有这个问题。考虑一下怎么设计比较优化。

---

## Message 115 [2026-03-17]

考虑到，比较老年份的引用往往不容易发生变化，是不是应该从老年份往新年份抓取比较合适？至少强制更新的时候应该这么处理。只更新新引用的时候可以从新年份往回抓。请根据这个建议仔细确认一下设计思路，千万不要弄错了。

---

## Message 116 [2026-03-18]

git提交，更新work notes

---

## Message 117 [2026-03-18]

user.md也要更新。请保证这样的工作流程：分析处理问题、修改代码并做可能的测试验证、更新worknotes和usermd，然后提交git项目。

---

## Message 118 [2026-03-18]

捕获一下这里的异常，输出一个网络有问题的提示。AttributeError: 'NoneType' object has no attribute 'get'
Failed to fetch basic info, exiting

---

## Message 119 [2026-03-20]

关注当前目录下的项目，现在整体的工作方案已经不错了，但是很多ip都会在30-38次引用页面访问后停止，请看一看有什么进一步优化的办法。

---

## Message 120 [2026-03-20]

有的页面在获取profile的时候是成功的，但是一开始获取页面就显示出错。

---

## Message 121 [2026-03-20]

可以用一个更复杂的休息机制，比如每8~12页，用一次3-6分钟的长休息。session每10页应该是会更新一次的吧？不过它的效果并不明显。延时适当增加也可以。

---

## Message 122 [2026-03-20]

第一页就失败的情况就暂时不更新了，因为换下一篇也应该一样会被封。其他的内容先更新一下。另外，我们有可能更新httpx吗？

---

## Message 123 [2026-03-20]

没有报错，改吧。

---

## Message 124 [2026-03-20]

重申一下我们的执行步骤：1. 讨论方案 2. 改代码 3. 验证代码正确 4. 记录worknotes 5. 记录用户的输入到user.md 6. git提交。

---

## Message 125 [2026-03-20]

更新之后出了问题：Failed to fetch basic info: Cannot Fetch from Google Scholar.

---

## Message 126 [2026-03-20]

是不是现在在使用httpx的proxy了？会不会跟这个有关？

---

## Message 127 [2026-03-20]

scholarly这个package已经好几年没有更新了，请通过提取网页链接确认一下各个关键部分它生成的url是否正确，是否需要进行更新。如果有需要的话，可以考虑在本地建立一个scholarly的新版本。

---

## Message 128 [2026-03-20]

我切换了ip，请把刚刚的测试再进行一下。

---

## Message 129 [2026-03-20]

我们能不能从web访问开始，重新确定一个更接近当前真实访问的url请求形式？（提供了作者主页URL：https://scholar.google.com.hk/citations?user=HF3-E9kAAAAJ&hl=en）

---

## Message 130 [2026-03-20]

（提供了 cURL 导出及 citation/year/pagination 的真实 URL 样本）

since 2022: https://scholar.google.com.hk/scholar?as_ylo=2022&hl=en&as_sdt=2005&sciodt=0,5&cites=...&scipsc=
翻页: https://scholar.google.com.hk/scholar?start=10&hl=en&as_sdt=2005&sciodt=0,5&as_ylo=2022&cites=...&scipsc=

---

## Message 131 [2026-03-20]

好的，全部修正他们

---

## Message 132 [2026-03-20]

继续处理，刚刚是我误操作。

---

## Message 133 [2026-03-20]

以这个url为例，为什么我web可以访问，但是在程序中就变成不行了？（URL: https://scholar.google.com/scholar?as_ylo=2021&as_yhi=2021&hl=en&as_sdt=2005&sciodt=0,5&cites=...）

---

## Message 134 [2026-03-20]

(Session reset: got_403 cleared, cookies preserved) 这个输出表示什么意思来着？

---

## Message 135 [2026-03-20]

如果程序提示了输入验证码，我能通过控制台做点什么，帮助程序通过验证码？

---

## Message 136 [2026-03-20]

用一个参数控制这个行为，比如叫--interactive-captcha

---

## Message 137 [2026-03-20]

程序有bug，首先让用户尝试的页面是profile页面，并不是引发验证码的scholar页面，此外，copy的curl是一个多行字符串，paste之后并未正常执行，而是选择等待6小时。

---

## Message 138 [2026-03-20]

上次那个等待用户粘贴curl的窗口最后失去响应了，请检查一下确认input没有问题。

---

## Message 139 [2026-03-20]

还是有bug，我按照提示进行了操作。程序提示导入了5个cookie，但是仍然又让我继续尝试验证码，但我打开对应的url，并不需要输入验证码。

---

## Message 140 [2026-03-20]

我在当前目录下的curl.txt里放了一个正常访问的请求，请参考比较一下。

---

## Message 141 [2026-03-20]

我觉得跟domain没什么关系，我获得cookie都是使用程序给定的url，那些都是.com domain的。

---

## Message 142 [2026-03-20]

retry 2 和 retry 3 似乎没有必要（同一 IP 在几秒内重试 Scholar 不会放行）。

---

## Message 143 [2026-03-21]

如果不是interactive模式，那么直接进入24小时等待，在等待期间，提示用户切换代理，并每小时检查一下用户输入。如果用户输入了ok，表示已经切换好了，那就继续进行尝试。

---

## Message 144 [2026-03-21]

粘贴 curl 时有时卡死，与 SSH/tmux 有关吗？

---

## Message 145 [2026-03-21]

在程序结束的时候输出一下本次进展，总共运行多少时间，成功访问了多少个页面，获取了多少个新引用。

---

## Message 146 [2026-03-21]

验证过程经常卡死，要注意检查。

---

## Message 147 [2026-03-21]

确认一下输入终结到底是最后一行无换行，还是额外输入一个空行。另外，如果是interactive模式，就不要记3次重试然后退出了，可以一直进行下去。

---

## Message 148 [2026-03-21]

对了，请把主要的功能更新也写到readme里，这样方便用户了解相关的情况。记得在里面写一下user.md和worknotes相关的情况。

---

## Message 149 [2026-03-21]

如果是interactive模式，就不需要清空或者重置session了。

---

## Message 150 [2026-03-21]

我发现在抽取某年份第5页引用的时候引发了验证码，验证通过之后为什么又从第1页开始抓取了？这里的机制是不是应该再确认一下？

---

## Message 151 [2026-03-21]

我觉得要区分是在同一次运行中，还是两次不同运行中的保存，同一次运行中可以保存的更精细一点，两次不同运行大概率就可以重新抓取了。

---

## Message 152 [2026-03-21]

解决每个问题之后，请我确认一下在进行git提交。希望每次都确定问题已经解决。

---

## Message 153 [2026-03-23]

请总结一下每次用户提出意见之后的工作模式，写到approach.md文件里。

---

## Message 154 [2026-03-23]

给这个程序加一个--help命令，输出所有可能的参数名称，否则不容易记得。

---

## Message 155 [2026-03-23]

确认一下，--force-refresh-citations 如果有skip和limit参数，应该只会影响对应论文的citation吧？

---

## Message 156 [2026-03-23]

注意一个问题，获一个论文的引用的时候，起始年份应该根据google scholar页面提供的引用年份进行，因为论文最终版本和arxiv时间并不一定一直。arxiv时间一般会提前很多。

---

## Message 157 [2026-03-23]

能不能以网页上的信息作为依据？

---

## Message 158 [2026-03-23]

好的，可以按照这个流程。请顺便统一一下输出形式，让日志显示每次waiting的时候也带上时间。

---

## Message 159 [2026-03-23]

好的，确认

---

## Message 160 [2026-03-23]

在force refresh citation的时候，上述最早年份的请求也要重新做。

---

## Message 161 [2026-03-23]

另外，current_year - 30是在干什么？不需要差这么久吧

---

## Message 162 [2026-03-23]

用不了10年，5年就够了。之前回退3年差不多也是这个意思。就统一成5年吧。作为probe不到的backup

---

## Message 163 [2026-03-23]

请检查一下各个文档中相关的内容，有需要的话也应该更新。

---

## Message 164 [2026-03-23]

scholar给了range就不用再提前了，因为它本身也不会包含更早的引用了。

---

## Message 165 [2026-03-23]

确认

---

## Message 166 [2026-03-23]

在refresh citation的过程中，如果发现抓取到的引用总数达到了scholar上显示的论文数，是不是也就可以停止了，不需要再继续更新了。或者这里加个--hard参数，如果激活，就强制全部重新抓取，如果没有这个参数，就提前停止。

---

## Message 167 [2026-03-23]

好的，确认前一个提交

---

## Message 168 [2026-03-23]

如果确实抓取的时候发现google scholar上有重复的信息，比如两个条目的title相同，但是可能有其他信息不同，这个重复的数字应该记下来，因为这个会影响反复抓取的效率。

---

## Message 169 [2026-03-23]

建议只有在forced refresh --hard的时候才清空这些记录，完全从头更新。

---

## Message 170 [2026-03-23]

确认

---
