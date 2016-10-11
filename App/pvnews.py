#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
from builtins import print
from logger import logger
import DB_Helper
import re
import sqlite3
import time
import UrlUtility
import pandas as pd
import sys


LoginUrl = 'http://www.pvnews.cn/e/enews/index.php'
BaseUrl = 'http://www.pvnews.cn'

Url_dir = {'多晶硅料': '/yuanshengduojing/index.php',
           '硅片晶圆': '/guipianhangqing/index.php',
           '晶硅电池': '/dianchipian/index.php',
           '电池组件': '/dianchizujian/index.php'}

Name_dir = {'多晶硅料': 'yuanshengduojing',
            '硅片晶圆': 'guipianhangqing',
            '晶硅电池': 'dianchipian',
            '电池组件': 'dianchizujian'}

Type_dir = {'多晶硅料': '1',
            '硅片晶圆': '2',
            '晶硅电池': '3',
            '电池组件': '4'}

pages = set()
hrefs = set()

session = requests.Session()
type_count = 1

def getAllhrefsFromSql(item_type, mark=None):
    '''mark == 0 url没被成功读取过 mark == 1 已经读取过'''
    conn = sqlite3.connect(DB_Helper.db_file)
    if mark is None:
        sqlstr = "select Id,url from %s where type     = '%s'" \
                 % (DB_Helper.PvNewsUrlSet_Table, item_type)
    else:
        sqlstr = "select Id,url from %s where type = '%s' and mark = %s" \
                 % (DB_Helper.PvNewsUrlSet_Table, item_type, mark)
    cursor = conn.execute(sqlstr)
    values = cursor.fetchall()
    cursor.close()
    conn.close()
    hrefList = set(values)
    return hrefList


def savehrefToSql(item_type):
    global hrefs
    newhref_count = 0
    hreflist = getAllhrefsFromSql(item_type)

    print("正在存储新的内链地址")
    conn = sqlite3.connect(DB_Helper.db_file)
    for href in hrefs:
        if not href in hreflist:
            insertSql = "insert into %s (url,type) values ('%s','%s' )" \
                        % (DB_Helper.PvNewsUrlSet_Table, href, item_type)
            conn.execute(insertSql)
            newhref_count += 1
    conn.commit()
    conn.close()
    print("内链地址更新完成 更新数量:%s" % newhref_count)


def getDataFromHref(item_type):
    global type_count
    # 获得还没采集数据的href地址
    targetlist = getAllhrefsFromSql(item_type, mark=0)
    conn = sqlite3.connect(DB_Helper.db_file)
    patten = re.compile(r"(?<=\d{1,2}月\d{2}日?)\w+(?=部分)")
    
    print("开始采集内链表格数据")
    for targethref in targetlist:
        urlid = targethref[0]
        suburl = targethref[1]
        ObjUrl = BaseUrl + suburl
        time.sleep(1)
        bsObj = UrlUtility.getBsObjFromUrl(ObjUrl, s=session)
        if bsObj is None:
            continue

        # 表格样式判断 获得表格第一行中div 标签的数量判断表格样式
        tablenode = bsObj.find('div', {'class': 'bencandy_nr'}).table
        trnode = tablenode.find('tr')
        tdlist = trnode.find_all('td')

        if tdlist == 1:
            tab_style = 'new'
        else:
            tab_style = 'old'

        # 页面类型获取
        title = bsObj.find('div', {'class': 'bencandy_title'}).get_text()
        dataDsc = patten.findall(title)
        if dataDsc[0] == '日':
            dataDsc = dataDsc[1:-1]
        
        # 日期
        subtitle = bsObj.find('div', {'class': 'bencandy_ftitle'})
        article_date = subtitle.get_text(strip=True).split(' ', 1)[0]

        # 表数据获得
        tablenode = bsObj.find('div', {'class': 'bencandy_nr'}).table
        df = pd.read_html(str(tablenode), header=0)[0]

        if tab_style == 'new':
            df2 = df.iloc[1:].copy()
            df2.columns = df.iloc[0].values
        else:
            df2 = df.iloc[1:, 0:-1].copy()
            df2.columns = df.columns.delete(0)
            df2 = df2.append(df.iloc[0, 1:])

        df2.insert(0, '日期', None)
        df2['date'] = article_date
        df2.insert(1, '数据类型', None)
        df2['src'] = dataDsc
    
        table_type = 1
        print("开始数据库存入")
        while 1:
            try:
                tablename = DB_Helper.PvNewsData_Table + ' ' + str(table_type)
                df2.to_sql(tablename, conn, if_exists='append', index=False)
            except sqlite3.OperationalError:
                if table_type > type_count:
                    logger.error("Url: %s 表存储失败", suburl)
                    break
                table_type += 1
            else:
                conn.execute("update %s set mark = 1 where Id = %s" % (DB_Helper.PvNewsUrlSet_Table, urlid))
                conn.commit()
                if table_type > type_count:
                    type_count = table_type
                break
        if type_count > 8:
            print("表格种类过多")
            break
        print("存入完成")
    conn.close()


def searchAllPages(url, name):
    '''获得所有页面链接'''
    global pages
    global hrefs

    ObjUrl = BaseUrl + url
    regular_str = r"\/%s\/index_*[0-9]*\.php" % name
    pages.add(url)
    print("开始读取页面:%s" % len(pages))

    time.sleep(2)
    bsObj = UrlUtility.getBsObjFromUrl(ObjUrl)
    if bsObj is None:
        return

    try:
        newtable = bsObj.find("div", {'class': 'list_list'})
        newlist = newtable.find_all('a')
    except AttributeError as e:
        logger.error("页面: %s  href列表 查询失败" % url)
    else:
        for item in newlist:
            hrefs.add(item.attrs['href'])

    links = bsObj.find_all('a', href=re.compile(regular_str))
    for link in links:
        if 'href' in link.attrs:
            newhref = link.attrs['href']
            # 有新页面
            if newhref not in pages:
                searchAllPages(newhref, name)


def getLoginParam(username, password):
    data = {'username': None, 'password': None, 'Submit': '登录'}

    data['ecmsfrom'] = ''
    data['enews'] = 'login'
    data['username'] = username
    data['password'] = password

    return data


def accountLogin(username, password):
    headers = {'User-Agent':
                   'Mozilla/5.0 (Windows NT 6.1) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/49.0.2623.112 '
                   'Safari/537.36'}

    data = getLoginParam(username, password)
    s = session.post(LoginUrl, data=data, headers=headers)

    if len(s.cookies) == 0:
        print("登录失败")
        return False
    else:
        print("登录完成")
        return True


def getDataByItem(item):
    global pages
    pages = set()

    item_type = Type_dir[item]
    item_url = Url_dir[item]
    item_name = Name_dir[item]
    print("%s :开始收集" % item)
    # # 递归查询
    # searchAllPages(item_url, item_name)
    #
    # # 存储内链地址
    # savehrefToSql(item_type)

    # 获取内链数据
    getDataFromHref(item_type)


if __name__ == '__main__':
    sys.setrecursionlimit(5000)
    
    if not accountLogin('cjsc', 'cjscdhl'):
        exit(0)
    getDataByItem('多晶硅料')
    
    # bsobj = UrlUtility.getBsObjFromUrl('http://www.pvnews.cn/yuanshengduojing/2010-11-05/645.html')
    
    # # 文章日期
    # subtitle = bsobj.find('div', {'class': 'bencandy_ftitle'})
    # article_date = subtitle.get_text(strip=True).split(' ', 1)[0]
    # print(article_date)
    #
    # tablenode = bsobj.find('div', {'class': 'bencandy_nr'}).table
    # df = pd.read_html(str(tablenode), header=0)[0]
    # df2 = df.iloc[1:, 0:-1]
    # df2.columns = df.columns.delete(0)
    # df2 = df2.append(df.iloc[0, 1:])
    # df2.insert(0, '产品', None)
    # df2['产品'] = df.iat[0, 0].replace(' ', '')
    # df2 = df2.sort_index()
    # print(df2)
