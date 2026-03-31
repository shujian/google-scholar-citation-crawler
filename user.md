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

## Message 62 [2026-03-31T00:00:00Z]

我主要使用中文，当前的工作环境是conda环境的scholar，相关工具都已经配置好了。如果需要安装新的工具，请使用conda相关的安装命令。

---

## Message 63 [2026-03-31T00:00:00Z]

请记住这两个文件要保持跟代码文件py一起更新并提交。

---
