# 독서기록장 v8.0 - Final Release
# 개발자: 권민혁 (wiredlife@daum.net)
# 주요 기능: ISBN 자동검색, 위키피디아 연결, 독서 보고서, 태그 관리, Excel 내보내기

import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
import requests
import json
import re
import time
import plotly.express as px
import plotly.graph_objects as go
from collections import Counter
from typing import Optional, Dict, List, Tuple
import hashlib
import io
import base64
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
import shutil
import os

# 도서관 정보나루 API 연동 클래스
class LibraryBookCollector:
    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = "https://data4library.kr/api/srchBooks"
        
    def get_book_metadata(self, isbn, progress_callback=None):
        """ISBN으로 도서관 정보나루에서 메타데이터 수집"""
        def update_progress(message):
            if progress_callback:
                progress_callback(message)
        
        # ISBN 정리
        isbn_clean = str(isbn).replace('-', '').replace(' ', '')
        
        # ISBN 형식 검증
        if not isbn_clean.isdigit() or len(isbn_clean) not in [10, 13]:
            return {
                'success': False,
                'message': f"잘못된 ISBN 형식: {isbn}",
                'suggestion': 'ISBN은 10자리 또는 13자리 숫자여야 합니다.'
            }
            
        params = {
            'authKey': self.api_key,
            'isbn13': isbn_clean,
            'format': 'json',
            'pageSize': 10
        }
        
        try:
            update_progress(f"도서관 정보나루에서 ISBN {isbn_clean} 검색 중...")
            
            response = requests.get(self.base_url, params=params, timeout=15)
            response.raise_for_status()
            
            data = response.json()
            
            # 응답 데이터 확인
            if not data.get('response') or not data['response'].get('docs'):
                return {
                    'success': False,
                    'message': '해당 ISBN으로 도서를 찾을 수 없습니다.',
                    'suggestion': '직접 입력해주세요.'
                }
                
            # 첫 번째 검색 결과 사용
            book_info = data['response']['docs'][0].get('doc', {})
            
            # 디버깅: API 응답 확인 (개발 중 임시)
            # st.write("API Response:", book_info)  # 디버깅용
            
            # 저자 정보 정리 (여러 저자 처리)
            authors_raw = book_info.get('authors', '')
            if isinstance(authors_raw, str) and authors_raw:
                # 세미콜론, 콤마로 구분된 저자명 처리
                authors = authors_raw.replace(';', ', ')
                # '지은이', '옮긴이' 등의 역할 표시 제거
                authors = re.sub(r'\s*\[.*?\]\s*', '', authors)  # [지은이], [옮긴이] 등 제거
                authors = re.sub(r'\s*(지은이|옮긴이|글|저|역)\s*', '', authors)
                authors = authors.strip(' ,;')
                if not authors:
                    authors = '정보 없음'
            else:
                authors = '정보 없음'
            
            # 주제분류 정보 조합
            class_name = book_info.get('class_nm', '')
            class_no = book_info.get('class_no', '')
            subject = class_name if class_name else '정보 없음'
            
            # 대출건수를 숫자로 변환
            loan_count = book_info.get('loan_count', 0)
            try:
                loan_count = int(loan_count) if loan_count else 0
            except (ValueError, TypeError):
                loan_count = 0
            
            update_progress("메타데이터 정리 중...")
            
            # 반환할 메타데이터 구조화
            metadata = {
                'success': True,
                'title': book_info.get('bookname', '정보 없음'),
                'author': authors,
                'publisher': book_info.get('publisher', '정보 없음'),
                'publication_year': book_info.get('publication_year', '정보 없음'),
                'isbn13': book_info.get('isbn13', isbn_clean),
                'subject': subject,
                'subject_code': class_no,
                'loan_count': loan_count,
                'cover_url': book_info.get('bookImageURL', ''),
                'detail_url': book_info.get('bookDtlUrl', ''),
                'volume': book_info.get('vol', ''),
                'series_isbn': book_info.get('set_isbn13', ''),
                'source': '도서관 정보나루'
            }
            
            update_progress("검색 완료!")
            return metadata
            
        except requests.exceptions.Timeout:
            return {
                'success': False,
                'message': 'API 응답 시간 초과입니다.',
                'suggestion': '잠시 후 다시 시도해주세요.'
            }
        except requests.exceptions.RequestException as e:
            return {
                'success': False,
                'message': f'API 요청 오류: {str(e)}',
                'suggestion': '네트워크 연결을 확인해주세요.'
            }
        except json.JSONDecodeError as e:
            return {
                'success': False,
                'message': 'API 응답 파싱 오류입니다.',
                'suggestion': '잠시 후 다시 시도해주세요.'
            }
        except Exception as e:
            return {
                'success': False,
                'message': f'예기치 않은 오류: {str(e)}',
                'suggestion': '직접 입력해주세요.'
            }

# API 키 설정
LIBRARY_API_KEY = "76ccad6c1d1e0f0c03de1dd3764cf5082f4f1dcf46a79b459c9d55cf8b9252de"

# 도서 수집기 인스턴스
book_collector = LibraryBookCollector(LIBRARY_API_KEY)

# Wikipedia/Wikidata 연동 클래스
class WikiConnector:
    def __init__(self):
        self.wiki_api = "https://ko.wikipedia.org/w/api.php"
        self.wikidata_api = "https://www.wikidata.org/w/api.php"
        self.cache = {}  # 간단한 메모리 캐시
        
    def search_wikipedia(self, query: str, lang: str = 'ko') -> Optional[Dict]:
        """위키피디아에서 검색"""
        try:
            # 캐시 확인
            cache_key = f"wiki_{lang}_{query}"
            if cache_key in self.cache:
                return self.cache[cache_key]
            
            # API 호출
            params = {
                'action': 'opensearch',
                'search': query,
                'limit': 5,
                'format': 'json',
                'origin': '*'
            }
            
            response = requests.get(
                f"https://{lang}.wikipedia.org/w/api.php",
                params=params,
                timeout=5
            )
            response.raise_for_status()
            
            data = response.json()
            if len(data) >= 4 and data[1]:
                results = []
                for i in range(min(len(data[1]), 3)):
                    results.append({
                        'title': data[1][i] if i < len(data[1]) else None,
                        'description': data[2][i] if i < len(data[2]) else '',
                        'url': data[3][i] if i < len(data[3]) else None
                    })
                
                result = {
                    'success': True,
                    'results': results,
                    'query': query
                }
                self.cache[cache_key] = result
                return result
            
            return {'success': False, 'message': '검색 결과가 없습니다'}
            
        except Exception as e:
            return {'success': False, 'message': str(e)}
    
    def search_wikidata(self, query: str, lang: str = 'ko') -> Optional[Dict]:
        """Wikidata에서 엔티티 검색"""
        try:
            # 캐시 확인
            cache_key = f"wikidata_{lang}_{query}"
            if cache_key in self.cache:
                return self.cache[cache_key]
            
            params = {
                'action': 'wbsearchentities',
                'search': query,
                'language': lang,
                'limit': 5,
                'format': 'json',
                'origin': '*'
            }
            
            response = requests.get(self.wikidata_api, params=params, timeout=5)
            response.raise_for_status()
            
            data = response.json()
            if data.get('search'):
                results = []
                for item in data['search'][:3]:
                    results.append({
                        'id': item.get('id'),
                        'label': item.get('label'),
                        'description': item.get('description', ''),
                        'url': f"https://www.wikidata.org/wiki/{item.get('id')}"
                    })
                
                result = {
                    'success': True,
                    'results': results,
                    'query': query
                }
                self.cache[cache_key] = result
                return result
            
            return {'success': False, 'message': '검색 결과가 없습니다'}
            
        except Exception as e:
            return {'success': False, 'message': str(e)}
    
    def get_page_summary(self, title: str, lang: str = 'ko') -> Optional[Dict]:
        """위키피디아 페이지 요약 가져오기"""
        try:
            url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            
            data = response.json()
            return {
                'success': True,
                'title': data.get('title'),
                'extract': data.get('extract'),
                'thumbnail': data.get('thumbnail', {}).get('source'),
                'url': data.get('content_urls', {}).get('desktop', {}).get('page')
            }
            
        except Exception as e:
            return {'success': False, 'message': str(e)}
    
    def get_full_article(self, title: str, lang: str = 'ko') -> Optional[Dict]:
        """위키피디아 전체 문서 내용 가져오기"""
        try:
            # MediaWiki API를 사용하여 전체 내용 가져오기
            url = f"https://{lang}.wikipedia.org/w/api.php"
            params = {
                'action': 'query',
                'format': 'json',
                'prop': 'extracts|pageimages',
                'titles': title,
                'exintro': False,  # 전체 내용 가져오기
                'explaintext': True,  # 평문 텍스트로
                'exsectionformat': 'plain',
                'piprop': 'original',
                'pilimit': 1
            }
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            pages = data.get('query', {}).get('pages', {})
            
            if pages:
                page_id = list(pages.keys())[0]
                page_data = pages[page_id]
                
                if 'extract' in page_data:
                    # 섹션 분리
                    content = page_data['extract']
                    sections = content.split('\n\n\n')  # 섹션 구분
                    
                    return {
                        'success': True,
                        'title': page_data.get('title'),
                        'content': content,
                        'sections': sections,
                        'image': page_data.get('original', {}).get('source') if 'original' in page_data else None
                    }
            
            return {'success': False, 'message': '문서를 찾을 수 없습니다'}
            
        except Exception as e:
            return {'success': False, 'message': str(e)}
    
    def search_book_entities(self, book_data: Dict) -> Dict:
        """책 정보를 기반으로 관련 엔티티 검색 (출판사 제외)"""
        entities = {
            'book': None,
            'author': None
        }
        
        # 책 제목으로 검색
        if book_data.get('title'):
            wiki_result = self.search_wikipedia(book_data['title'])
            if wiki_result and wiki_result.get('success'):
                entities['book'] = wiki_result.get('results', [None])[0]
        
        # 저자로 검색
        if book_data.get('author'):
            # 저자명 정리
            author_clean = clean_author_name(book_data['author'])
            if author_clean:
                # 첫 번째 저자만 검색 (콤마로 구분된 경우)
                first_author = author_clean.split(',')[0].strip()
                wiki_result = self.search_wikipedia(first_author)
                if wiki_result and wiki_result.get('success'):
                    entities['author'] = wiki_result.get('results', [None])[0]
                
                # Wikidata에서도 검색
                wikidata_result = self.search_wikidata(first_author)
                if wikidata_result and wikidata_result.get('success'):
                    if entities['author']:
                        entities['author']['wikidata'] = wikidata_result.get('results', [None])[0]
        
        return entities

# Wiki 커넥터 인스턴스
wiki_connector = WikiConnector()

# 헬퍼 함수들
def safe_get_value(obj, key, default=''):
    """딕셔너리나 Series에서 안전하게 값 가져오기"""
    try:
        value = obj.get(key, default) if hasattr(obj, 'get') else obj[key]
        return value if pd.notna(value) else default
    except (KeyError, TypeError, AttributeError):
        return default

def clean_author_name(author_str):
    """저자명 정리 함수"""
    if not author_str or pd.isna(author_str):
        return ''
    
    # 역할 표시 제거
    cleaned = re.sub(r'\s*\[.*?\]\s*', '', str(author_str))
    cleaned = re.sub(r'\s*(지은이|옮긴이|글|저|역|편저|감수)\s*', '', cleaned)
    cleaned = cleaned.strip(' ,;')
    return cleaned

def validate_and_format_isbn(isbn):
    """ISBN 유효성 검사 및 13자리 변환"""
    # 공백, 하이픈 제거
    isbn = re.sub(r'[^0-9X]', '', isbn.upper())
    
    if len(isbn) == 10:
        # ISBN-10을 13자리로 변환
        isbn12 = '978' + isbn[:-1]
        
        # 체크섬 계산
        checksum = 0
        for i, digit in enumerate(isbn12):
            weight = 1 if i % 2 == 0 else 3
            checksum += int(digit) * weight
        
        check_digit = (10 - (checksum % 10)) % 10
        isbn13 = isbn12 + str(check_digit)
        
        return {
            'valid': True,
            'isbn13': isbn13,
            'message': f'ISBN-10을 13자리로 변환: {isbn13}'
        }
    
    elif len(isbn) == 13:
        # ISBN-13 체크섬 검증
        try:
            checksum = 0
            for i, digit in enumerate(isbn[:-1]):
                weight = 1 if i % 2 == 0 else 3
                checksum += int(digit) * weight
            
            expected_check = (10 - (checksum % 10)) % 10
            actual_check = int(isbn[-1])
            
            if expected_check == actual_check:
                return {
                    'valid': True,
                    'isbn13': isbn,
                    'message': 'ISBN-13이 유효합니다'
                }
            else:
                return {
                    'valid': False,
                    'message': f'ISBN-13 체크섬 오류 (예상: {expected_check}, 실제: {actual_check})'
                }
        except (ValueError, IndexError):
            return {
                'valid': False,
                'message': 'ISBN-13 형식이 올바르지 않습니다'
            }
    
    else:
        return {
            'valid': False,
            'message': f'ISBN은 10자리 또는 13자리여야 합니다 (입력: {len(isbn)}자리)'
        }

def search_book_by_isbn(isbn13, progress_callback=None):
    """도서관 정보나루 API로 ISBN 검색"""
    return book_collector.get_book_metadata(isbn13, progress_callback)

# 데이터 내보내기 함수들
def export_to_excel(df: pd.DataFrame) -> bytes:
    """DataFrame을 Excel 파일로 변환"""
    output = io.BytesIO()
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='독서 기록', index=False)
        
        # 워크시트 스타일링
        workbook = writer.book
        worksheet = writer.sheets['독서 기록']
        
        # 헤더 스타일
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
        header_alignment = Alignment(horizontal="center", vertical="center")
        
        for cell in worksheet[1]:
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_alignment
        
        # 컬럼 너비 자동 조정
        for column in worksheet.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            worksheet.column_dimensions[column_letter].width = adjusted_width
    
    output.seek(0)
    return output.getvalue()


def create_backup(conn: sqlite3.Connection) -> bytes:
    """데이터베이스 전체 백업 생성"""
    backup_path = f"reading_log_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    
    # 백업 데이터베이스 생성
    backup_conn = sqlite3.connect(':memory:')
    conn.backup(backup_conn)
    
    # 메모리에서 바이트로 변환
    output = io.BytesIO()
    for line in backup_conn.iterdump():
        output.write(f"{line}\n".encode('utf-8'))
    
    backup_conn.close()
    output.seek(0)
    return output.getvalue()

def restore_from_backup(uploaded_file) -> bool:
    """백업 파일에서 데이터베이스 복원"""
    try:
        # 현재 DB 백업 (안전을 위해)
        shutil.copy2("reading_log.db", f"reading_log_before_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
        
        # 업로드된 파일을 새 DB로 저장
        with open("reading_log_temp.db", "wb") as f:
            f.write(uploaded_file.getbuffer())
        
        # 검증: 테이블 구조 확인
        temp_conn = sqlite3.connect("reading_log_temp.db")
        cursor = temp_conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        temp_conn.close()
        
        if not any('books' in table for table in tables):
            os.remove("reading_log_temp.db")
            return False
        
        # 기존 DB를 새 DB로 교체
        os.remove("reading_log.db")
        os.rename("reading_log_temp.db", "reading_log.db")
        
        return True
    except Exception as e:
        st.error(f"복원 중 오류 발생: {str(e)}")
        if os.path.exists("reading_log_temp.db"):
            os.remove("reading_log_temp.db")
        return False

# 보고서 생성 함수들
def generate_reading_report(df: pd.DataFrame, period: str = "all", year: int = None, month: int = None) -> str:
    """독서 보고서 생성 (마크다운 형식)"""
    
    # 기간별 필터링
    if period == "month" and year and month:
        start_date = f"{year}-{month:02d}-01"
        end_date = f"{year}-{month:02d}-31"
        period_df = df[(df['added_date'] >= start_date) & (df['added_date'] <= end_date)]
        period_text = f"{year}년 {month}월"
    else:
        period_df = df
        period_text = "전체 기간"
    
    if len(period_df) == 0:
        return f"# 📚 {period_text} 독서 보고서\n\n해당 기간에 기록된 책이 없습니다."
    
    # 통계 계산
    total_books = len(period_df)
    completed_books = len(period_df[period_df['status'] == '읽음'])
    reading_books = len(period_df[period_df['status'] == '읽는 중'])
    want_books = len(period_df[period_df['status'] == '읽고 싶음'])
    
    # 평균 평점
    avg_rating = period_df[period_df['rating'] > 0]['rating'].mean() if any(period_df['rating'] > 0) else 0
    
    # 총 페이지
    total_pages = period_df['pages'].sum() if 'pages' in period_df.columns else 0
    
    # 보고서 생성
    report = f"""# 📚 {period_text} 독서 보고서

## 📊 독서 통계 요약
- **총 책 수**: {total_books}권
- **완독**: {completed_books}권
- **읽는 중**: {reading_books}권  
- **읽고 싶음**: {want_books}권
- **평균 평점**: ⭐ {avg_rating:.1f}
- **총 페이지**: {total_pages:,}페이지

"""
    
    # 완독한 책 목록
    if completed_books > 0:
        completed_df = period_df[period_df['status'] == '읽음'].sort_values('rating', ascending=False)
        report += "## ✅ 완독한 책들\n\n"
        
        for _, book in completed_df.iterrows():
            rating_stars = "⭐" * int(book['rating']) if book['rating'] > 0 else ""
            report += f"### 📖 {book['title']}\n"
            report += f"- **저자**: {book['author']}\n"
            if rating_stars:
                report += f"- **평점**: {rating_stars}\n"
            if book.get('memo'):
                report += f"- **메모**: {book['memo']}\n"
            report += "\n"
    
    # 장르/주제 분석
    if 'subject' in period_df.columns:
        subjects = period_df['subject'].value_counts().head(5)
        if len(subjects) > 0:
            report += "## 🎯 주요 관심 분야\n\n"
            for subject, count in subjects.items():
                if subject and subject != '정보 없음':
                    report += f"- **{subject}**: {count}권\n"
            report += "\n"
    
    # 인기 저자
    if 'author' in period_df.columns:
        authors = period_df['author'].value_counts().head(3)
        if len(authors) > 0:
            report += "## 👤 자주 읽은 저자\n\n"
            for author, count in authors.items():
                if author and author != '정보 없음':
                    report += f"- **{author}**: {count}권\n"
            report += "\n"
    
    # 태그 분석
    if 'tags' in period_df.columns:
        all_tags = []
        for tags in period_df['tags'].dropna():
            if tags:
                all_tags.extend(tags.split(','))
        
        if all_tags:
            tag_counts = Counter(all_tags)
            report += "## 🏷️ 주요 태그\n\n"
            for tag, count in tag_counts.most_common(5):
                report += f"- **#{tag.strip()}**: {count}권\n"
            report += "\n"
    
    # 추천 도서 (높은 평점)
    high_rated = period_df[period_df['rating'] >= 4].sort_values('rating', ascending=False).head(3)
    if len(high_rated) > 0:
        report += "## 🌟 이번 기간 베스트 도서\n\n"
        for idx, (_, book) in enumerate(high_rated.iterrows(), 1):
            report += f"**{idx}. {book['title']}** - {book['author']}\n"
            if book.get('memo'):
                report += f"   > {book['memo']}\n"
            report += "\n"
    
    # 맺음말
    report += f"""
---
*이 보고서는 {datetime.now().strftime('%Y년 %m월 %d일')}에 생성되었습니다.*  
*📚 독서기록장 v7.0으로 작성*
"""
    
    return report

def generate_monthly_summary(df: pd.DataFrame, year: int, month: int) -> Dict:
    """월별 독서 요약 데이터 생성"""
    start_date = f"{year}-{month:02d}-01"
    end_date = f"{year}-{month:02d}-31"
    
    # 해당 월 데이터 필터링
    month_df = df[(df['added_date'] >= start_date) & (df['added_date'] <= end_date)]
    
    # 해당 월에 읽은 책 (상태가 '읽음'인 책)
    completed_df = df[(df['status'] == '읽음') & 
                     (df['added_date'] >= start_date) & 
                     (df['added_date'] <= end_date)]
    
    summary = {
        'year': year,
        'month': month,
        'total_added': len(month_df),
        'completed': len(completed_df),
        'pages_read': completed_df['pages'].sum() if len(completed_df) > 0 else 0,
        'avg_rating': completed_df['rating'].mean() if len(completed_df) > 0 else 0,
        'top_tags': [],
        'books': month_df.to_dict('records')
    }
    
    # 태그 분석
    if 'tags' in month_df.columns:
        all_tags = []
        for tags in month_df['tags'].dropna():
            if tags:
                all_tags.extend(tags.split(','))
        if all_tags:
            tag_counts = Counter(all_tags)
            summary['top_tags'] = tag_counts.most_common(3)
    
    return summary


# 페이지 설정
st.set_page_config(
    page_title="내 독서기록장",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 데이터베이스 초기화 함수
@st.cache_resource
def init_database():
    """SQLite 데이터베이스 초기화 및 테이블 생성"""
    try:
        db_path = "reading_log.db"
        conn = sqlite3.connect(db_path, check_same_thread=False)
        
        # 테이블 생성 (태그 필드 추가)
        conn.execute('''
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            isbn TEXT,
            title TEXT NOT NULL,
            author TEXT,
            publisher TEXT,
            publication_year TEXT,
            subject TEXT,
            loan_count INTEGER DEFAULT 0,
            cover_url TEXT,
            rating INTEGER DEFAULT 3,
            status TEXT DEFAULT '읽고 싶음',
            memo TEXT,
            tags TEXT,
            pages INTEGER DEFAULT 0,
            added_date TEXT,
            updated_date TEXT
        )
        ''')
        
        # 독서 목표 테이블 생성
        conn.execute('''
        CREATE TABLE IF NOT EXISTS reading_goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year INTEGER UNIQUE,
            goal_count INTEGER,
            created_date TEXT
        )
        ''')
        
        # 기존 테이블에 새 컬럼 추가 (마이그레이션)
        cursor = conn.cursor()
        
        try:
            # 현재 컬럼 확인
            cursor.execute("PRAGMA table_info(books)")
            columns = [column[1] for column in cursor.fetchall()]
            
            # 누락된 컬럼 추가
            migration_performed = False
            
            migrations = [
                ('tags', 'TEXT'),
                ('pages', 'INTEGER DEFAULT 0'),
                ('wiki_links', 'TEXT'),  # JSON 형태로 위키 링크 저장
                ('last_wiki_search', 'TEXT')  # 마지막 위키 검색 시간
            ]
            
            for col_name, col_type in migrations:
                if col_name not in columns:
                    try:
                        cursor.execute(f"ALTER TABLE books ADD COLUMN {col_name} {col_type}")
                        migration_performed = True
                    except sqlite3.OperationalError:
                        pass  # 이미 존재하는 경우 무시
            
            if migration_performed:
                st.info("📚 데이터베이스가 최신 버전으로 업데이트되었습니다.")
        
        except Exception as e:
            st.warning(f"마이그레이션 확인 중 문제 발생: {e}")
        
        conn.commit()
        return conn
    
    except Exception as e:
        st.error(f"데이터베이스 초기화 오류: {e}")
        st.stop()

# 데이터베이스 연결
conn = init_database()

# 세션 상태 초기화
if 'search_result' not in st.session_state:
    st.session_state.search_result = None
if 'search_attempted' not in st.session_state:
    st.session_state.search_attempted = False

# 데이터베이스 함수들
def add_book_to_db(book_data):
    """책을 데이터베이스에 추가"""
    try:
        cursor = conn.cursor()
        cursor.execute('''
        INSERT INTO books (isbn, title, author, publisher, publication_year, subject, loan_count, cover_url, rating, status, memo, tags, pages, added_date, updated_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            book_data['isbn'],
            book_data['title'],
            book_data['author'],
            book_data['publisher'],
            book_data.get('publication_year', ''),
            book_data.get('subject', ''),
            book_data.get('loan_count', 0),
            book_data.get('cover_url', ''),
            book_data['rating'],
            book_data['status'],
            book_data['memo'],
            book_data.get('tags', ''),
            book_data.get('pages', 0),
            book_data['added_date'],
            book_data['added_date']
        ))
        conn.commit()
        return True
    except Exception as e:
        st.error(f"데이터베이스 저장 오류: {e}")
        return False

def get_books_from_db():
    """데이터베이스에서 모든 책 조회"""
    try:
        df = pd.read_sql_query("SELECT * FROM books ORDER BY added_date DESC", conn)
        # None 값을 빈 문자열로 변환
        for col in df.columns:
            if df[col].dtype == 'object':
                df[col] = df[col].fillna('')
        return df
    except Exception as e:
        st.error(f"데이터베이스 조회 오류: {e}")
        return pd.DataFrame()

def update_book_in_db(book_id, book_data):
    """책 정보 업데이트"""
    try:
        cursor = conn.cursor()
        cursor.execute('''
        UPDATE books 
        SET isbn=?, title=?, author=?, publisher=?, publication_year=?, subject=?, loan_count=?, cover_url=?, rating=?, status=?, memo=?, tags=?, pages=?, updated_date=?
        WHERE id=?
        ''', (
            book_data['isbn'],
            book_data['title'],
            book_data['author'],
            book_data['publisher'],
            book_data.get('publication_year', ''),
            book_data.get('subject', ''),
            book_data.get('loan_count', 0),
            book_data.get('cover_url', ''),
            book_data['rating'],
            book_data['status'],
            book_data['memo'],
            book_data.get('tags', ''),
            book_data.get('pages', 0),
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            book_id
        ))
        conn.commit()
        return True
    except Exception as e:
        st.error(f"데이터베이스 업데이트 오류: {e}")
        return False

def delete_book_from_db(book_id):
    """책 삭제"""
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM books WHERE id=?", (book_id,))
        conn.commit()
        return True
    except Exception as e:
        st.error(f"데이터베이스 삭제 오류: {e}")
        return False

def get_reading_goal(year):
    """연도별 독서 목표 조회"""
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT goal_count FROM reading_goals WHERE year=?", (year,))
        result = cursor.fetchone()
        return result[0] if result else None
    except:
        return None

def set_reading_goal(year, goal_count):
    """연도별 독서 목표 설정"""
    try:
        cursor = conn.cursor()
        # UPSERT 패턴 사용 (SQLite 3.24.0+)
        cursor.execute('''
        INSERT INTO reading_goals (year, goal_count, created_date)
        VALUES (?, ?, ?)
        ON CONFLICT(year) DO UPDATE SET 
            goal_count = excluded.goal_count,
            created_date = excluded.created_date
        ''', (year, goal_count, datetime.now().strftime("%Y-%m-%d")))
        conn.commit()
        return True
    except sqlite3.Error as e:
        # 구버전 SQLite 호환성을 위한 폴백
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM reading_goals WHERE year=?", (year,))
            cursor.execute('''
            INSERT INTO reading_goals (year, goal_count, created_date)
            VALUES (?, ?, ?)
            ''', (year, goal_count, datetime.now().strftime("%Y-%m-%d")))
            conn.commit()
            return True
        except:
            return False

def get_all_tags():
    """모든 태그 가져오기"""
    df = get_books_from_db()
    if len(df) == 0:
        return []
    
    # tags 컬럼이 없으면 빈 리스트 반환
    if 'tags' not in df.columns:
        return []
    
    all_tags = []
    for tags in df['tags'].dropna():
        if tags:
            all_tags.extend([tag.strip() for tag in tags.split(',')])
    
    return list(set(all_tags))


def get_reading_insights(df):
    """독서 패턴 인사이트 생성"""
    insights = []
    
    if len(df) == 0:
        return insights
    
    # 최근 3개월 데이터 분석
    three_months_ago = datetime.now() - timedelta(days=90)
    recent_df = df[pd.to_datetime(df['added_date']) >= three_months_ago]
    
    if len(recent_df) > 0:
        # 장르별 변화 분석
        if 'subject' in recent_df.columns:
            recent_subjects = recent_df['subject'].value_counts()
            if len(recent_subjects) > 0:
                top_subject = recent_subjects.index[0]
                if top_subject and top_subject != '정보 없음':
                    insights.append(f"💡 최근 3개월간 {top_subject} 분야 책을 가장 많이 읽으셨어요!")
        
        # 저자 연속 독서 패턴
        if 'author' in recent_df.columns:
            recent_sorted = recent_df.sort_values('added_date')
            prev_author = None
            consecutive_count = 0
            
            for author in recent_sorted['author']:
                if author == prev_author and author and author != '정보 없음':
                    consecutive_count += 1
                    if consecutive_count >= 2:
                        insights.append(f"📚 {author} 작가의 책을 연속으로 {consecutive_count + 1}권 읽으셨네요!")
                        break
                else:
                    consecutive_count = 0
                    prev_author = author
        
        # 독서 속도 분석
        read_books = recent_df[recent_df['status'] == '읽음']
        if len(read_books) > 3:
            books_per_month = len(read_books) / 3
            insights.append(f"📈 최근 월평균 {books_per_month:.1f}권을 읽고 계십니다!")
    
    # 평점 패턴
    if 'rating' in df.columns:
        high_rated = df[df['rating'] >= 4]
        if len(high_rated) > 5:
            if 'author' in high_rated.columns:
                fav_authors = high_rated['author'].value_counts()
                if len(fav_authors) > 0 and fav_authors.iloc[0] > 1:
                    insights.append(f"⭐ {fav_authors.index[0]} 작가의 책에 높은 평점을 주시는군요!")
    
    return insights

# 메인 헤더
st.title("📚 내 독서기록장")
st.markdown("**📖 나만의 스마트한 독서 기록 관리**")
st.markdown("---")

# 데이터베이스 상태 표시
db_info = get_books_from_db()
st.sidebar.success(f"💾 데이터베이스 연결됨\n📖 총 {len(db_info)}권 저장됨")

# 사이드바 네비게이션
st.sidebar.title("📖 메뉴")
menu = st.sidebar.selectbox(
    "원하는 기능을 선택하세요",
    ["📖 책 추가하기", "📋 내 도서목록", "✏️ 책 수정/삭제", "📊 독서 대시보드", "🎯 독서 목표", "📈 저자/출판사 분석", "🏷️ 태그 관리", "🌐 위키 연결", "💡 추천도서 조회", "📝 독서 보고서", "🔧 데이터 관리"]
)

# 📖 책 추가하기 메뉴 (정보나루 API 연동)
if menu == "📖 책 추가하기":
    st.header("새로운 책 추가")
    st.markdown("**🎯 도서관 정보나루 API로 풍부한 메타데이터 자동 수집**")
    
    # 입력 폼
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("📝 책 정보 입력")
        
        # ISBN 입력 섹션
        st.markdown("**🔍 자동 검색 (추천)**")
        
        # ISBN 입력과 검색 버튼을 같은 줄에
        isbn_col1, isbn_col2 = st.columns([3, 1])
        
        with isbn_col1:
            isbn = st.text_input(
                "ISBN (10자리 또는 13자리)", 
                placeholder="9788936434267",
                help="책 뒷면의 바코드 번호를 입력하세요",
                key="isbn_input"
            )
        
        with isbn_col2:
            st.markdown("<br>", unsafe_allow_html=True)  # 버튼 높이 맞추기
            search_clicked = st.button("🔍 자동검색", type="primary", use_container_width=True)
        
        # 자동검색 실행
        if search_clicked and isbn:
            st.session_state.search_attempted = True
            
            # 진행 상황 표시
            with st.spinner("📚 도서관 정보나루에서 책 정보를 검색하는 중입니다..."):
                # 프로그레스 바
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                def update_progress(message):
                    status_text.text(message)
                
                # API 검색 실행
                try:
                    update_progress("ISBN 검증 중...")
                    progress_bar.progress(20)
                    
                    # ISBN 검증
                    isbn_result = validate_and_format_isbn(isbn)
                    
                    if isbn_result['valid']:
                        isbn13 = isbn_result['isbn13']
                        
                        update_progress("도서관 정보나루 API 호출 중...")
                        progress_bar.progress(40)
                        
                        # 실제 검색
                        search_result = search_book_by_isbn(isbn13, update_progress)
                        
                        progress_bar.progress(100)
                        status_text.empty()
                        progress_bar.empty()
                        
                        st.session_state.search_result = search_result
                        
                        if search_result['success']:
                            st.success(f"✅ 책을 찾았습니다! ({search_result['source']})")
                            
                            # 검색 성공 정보 표시
                            if search_result.get('loan_count', 0) > 0:
                                st.info(f"📊 전국 도서관 대출 {search_result['loan_count']:,}회")
                            
                            st.balloons()
                        else:
                            st.warning(f"⚠️ {search_result['message']}")
                            if 'suggestion' in search_result:
                                st.info(f"💡 {search_result['suggestion']}")
                    else:
                        progress_bar.empty()
                        status_text.empty()
                        st.error(f"❌ {isbn_result['message']}")
                        st.session_state.search_result = None
                
                except Exception as e:
                    progress_bar.empty()
                    status_text.empty()
                    st.error(f"🚫 검색 중 오류가 발생했습니다: {str(e)}")
                    st.info("💡 서버에 일시적인 문제가 있을 수 있습니다. 직접 입력해주세요.")
                    st.session_state.search_result = None
        
        elif search_clicked and not isbn:
            st.error("❌ ISBN을 입력해주세요!")
        
        # 검색 결과가 있으면 자동으로 입력창에 채우기
        auto_title = ""
        auto_author = ""
        auto_publisher = ""
        auto_year = ""
        auto_subject = ""
        
        if (st.session_state.search_result and 
            st.session_state.search_result['success']):
            
            result = st.session_state.search_result
            auto_title = result.get('title', '')
            auto_author = result.get('author', '')
            auto_publisher = result.get('publisher', '')
            auto_year = result.get('publication_year', '')
            auto_subject = result.get('subject', '')
            
            # 검색 결과 요약 표시
            # 저자명 정리 안내
            if '지은이' in auto_author or '옮긴이' in auto_author or '[' in auto_author:
                st.warning("💡 저자명에 '지은이', '옮긴이' 등이 포함되어 있습니다. 아래 입력창에서 이름만 남겨주세요.")
            
            st.success(f"""
            **📖 검색 결과:**
            - **제목**: {auto_title}
            - **저자**: {auto_author}
            - **출판사**: {auto_publisher}
            - **출판년도**: {auto_year}
            - **주제분류**: {auto_subject}
            """)
            
            # 책 표지 표시 (있는 경우)
            if result.get('cover_url'):
                try:
                    st.image(result['cover_url'], width=150, caption="책 표지")
                except:
                    pass
        
        # 구분선
        st.markdown("---")
        st.markdown("**✏️ 직접 입력 또는 수정**")
        
        # 입력창들 (자동검색 결과로 초기값 설정)
        title = st.text_input(
            "책 제목", 
            value=auto_title,
            placeholder="예: 아몬드",
            help="자동검색 결과가 있으면 자동으로 채워집니다"
        )
        
        # 저자명 정리
        clean_author = clean_author_name(auto_author) if auto_author else ''
            
        author = st.text_input(
            "저자 (이름만 입력)", 
            value=clean_author,
            placeholder="예: 손원평",
            help="💡 저자 이름만 입력하세요. '지은이', '옮긴이', '[저]' 등은 자동으로 제거됩니다. 여러 명일 경우 콤마로 구분하세요."
        )
        
        publisher = st.text_input(
            "출판사", 
            value=auto_publisher,
            placeholder="예: 창비"
        )
        
        # 개인 기록
        st.markdown("**⭐ 개인 기록**")
        rating = st.slider("평점", 1, 5, 3)
        status = st.selectbox("읽기 상태", ["읽고 싶음", "읽는 중", "읽음"])
        
        # 태그 시스템
        existing_tags = get_all_tags()
        selected_tags = st.multiselect(
            "태그 선택 (기존 태그)",
            options=existing_tags,
            help="기존 태그를 선택하거나 아래에 새로운 태그를 입력하세요"
        )
        
        new_tags_input = st.text_input(
            "새로운 태그 추가",
            placeholder="태그1, 태그2, 태그3 (콤마로 구분)",
            help="새로운 태그를 콤마로 구분하여 입력하세요"
        )
        
        pages = st.number_input("페이지 수", min_value=0, value=0, help="선택사항")
        memo = st.text_area("메모", placeholder="이 책에 대한 생각을 자유롭게 적어보세요")
        
        # 추가 버튼
        if st.button("📚 책 추가하기", type="secondary", use_container_width=True):
            if title:  # 제목이 있으면 저장
                # 태그 합치기
                all_tags = selected_tags.copy() if selected_tags else []
                if new_tags_input:
                    new_tags = [tag.strip() for tag in new_tags_input.split(',') if tag.strip()]
                    all_tags.extend(new_tags)
                tags_str = ', '.join(all_tags) if all_tags else ''
                
                new_book = {
                    'isbn': isbn if isbn else '',
                    'title': title,
                    'author': author,
                    'publisher': publisher,
                    'publication_year': auto_year,
                    'subject': auto_subject,
                    'loan_count': st.session_state.search_result.get('loan_count', 0) if st.session_state.search_result and st.session_state.search_result['success'] else 0,
                    'cover_url': st.session_state.search_result.get('cover_url', '') if st.session_state.search_result and st.session_state.search_result['success'] else '',
                    'rating': rating,
                    'status': status,
                    'memo': memo,
                    'tags': tags_str,
                    'pages': pages,
                    'added_date': datetime.now().strftime("%Y-%m-%d %H:%M")
                }
                
                if add_book_to_db(new_book):
                    st.success(f"✅ '{new_book['title']}' 책이 영구 저장되었습니다!")
                    
                    # 성공 시 입력창 초기화
                    st.session_state.search_result = None
                    st.session_state.search_attempted = False
                    
                    st.balloons()
                    st.rerun()  # 페이지 새로고침
                else:
                    st.error("❌ 책 저장에 실패했습니다!")
            else:
                st.error("❌ 책 제목을 입력해주세요!")
    
    with col2:
        st.subheader("📋 사용 가이드")
        
        # 자동검색 상태에 따른 가이드 표시
        if st.session_state.search_attempted and st.session_state.search_result:
            if st.session_state.search_result['success']:
                st.success("""
                **🎉 자동검색 성공!**
                
                ✅ 도서관 정보나루에서 풍부한 메타데이터를 가져왔습니다
                ✅ 출판년도, 주제분류, 대출통계까지 자동 입력
                ✅ 필요시 정보를 수정할 수 있습니다
                ✅ 평점과 상태만 선택하면 완료!
                """)
            else:
                st.warning("""
                **⚠️ 자동검색 실패**
                
                다음 이유 중 하나일 수 있습니다:
                - 해당 ISBN이 도서관 데이터베이스에 없음
                - 잘못된 ISBN 입력
                - API 서버 일시적 오류
                
                💡 직접 입력으로 진행해주세요!
                """)
        else:
            st.info("""
            **🔍 자동검색 사용법:**
            
            1. **ISBN 입력**: 책 뒷면 바코드 숫자
            2. **자동검색 클릭**: 🔍 버튼 누르기
            3. **결과 확인**: 풍부한 메타데이터 자동 입력
            4. **개인 기록**: 평점, 상태, 메모 입력
            5. **저장**: 📚 책 추가하기 버튼
            
            **🎯 자동으로 수집되는 정보:**
            - 제목, 저자, 출판사, 출판년도
            - 주제분류, 전국 대출통계
            - 책 표지 이미지 (제공시)
            """)
        
        # 테스트용 ISBN 안내
        with st.expander("🧪 테스트용 인기 도서 ISBN"):
            st.write("""
            **인기 도서 ISBN으로 테스트:**
            - `9788936456788` - 아몬드 (손원평)
            - `898371154X` - 코스모스 (칼 세이건)
            - `9788937460449` - 데미안 (헤르만 헤세)
            """)
        
        # API 정보
        with st.expander("🔧 API 정보"):
            st.write("""
            **데이터 소스:** 도서관 정보나루 (국립중앙도서관)
            **제공 정보:**
            - 서지정보 (제목, 저자, 출판사, 출판년도)
            - 주제분류 (KDC 십진분류법)
            - 전국 도서관 대출통계
            - 책 표지 이미지
            
            **검색 시간:** 보통 2-3초 소요
            **안정성:** 공공 API로 높은 신뢰도
            """)
        
        # 현재 저장된 책 수 표시
        current_books = get_books_from_db()
        if len(current_books) > 0:
            col_metric1, col_metric2 = st.columns(2)
            with col_metric1:
                st.metric("💾 저장된 책", f"{len(current_books)}권")
            with col_metric2:
                total_loan = current_books['loan_count'].sum() if 'loan_count' in current_books.columns else 0
                st.metric("📊 총 대출통계", f"{total_loan:,}회")
            
            # 최근 추가된 책 표시
            if len(current_books) > 0:
                latest_book = current_books.iloc[0]  # 가장 최근 책
                st.write(f"**최근 추가:** {latest_book['title']}")

elif menu == "📋 내 도서목록":
    st.header("내가 추가한 책들")
    
    df = get_books_from_db()
    
    if len(df) == 0:
        st.warning("📚 아직 추가된 책이 없습니다. '책 추가하기' 메뉴에서 책을 추가해보세요!")
    else:
        # 자동 인사이트 표시
        insights = get_reading_insights(df)
        if insights:
            with st.expander("💡 독서 인사이트", expanded=True):
                for insight in insights:
                    st.info(insight)
        
        # 필터 옵션
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            status_filter = st.selectbox("상태 필터", ["전체", "읽고 싶음", "읽는 중", "읽음"])
        with col2:
            rating_filter = st.selectbox("평점 필터", ["전체", "⭐ 1점", "⭐⭐ 2점", "⭐⭐⭐ 3점", "⭐⭐⭐⭐ 4점", "⭐⭐⭐⭐⭐ 5점"])
        with col3:
            # 태그 필터
            all_tags = get_all_tags()
            tag_filter = st.selectbox("태그 필터", ["전체"] + all_tags)
        with col4:
            search_text = st.text_input("🔍 제목/저자 검색", placeholder="검색어 입력")
        
        # 필터 적용
        filtered_df = df.copy()
        if status_filter != "전체":
            filtered_df = filtered_df[filtered_df['status'] == status_filter]
        if rating_filter != "전체":
            rating_num = int(rating_filter.split()[1][0])
            filtered_df = filtered_df[filtered_df['rating'] == rating_num]
        if tag_filter != "전체" and 'tags' in filtered_df.columns:
            filtered_df = filtered_df[filtered_df['tags'].str.contains(tag_filter, case=False, na=False)]
        if search_text:
            mask = filtered_df['title'].str.contains(search_text, case=False, na=False) | \
                   filtered_df['author'].str.contains(search_text, case=False, na=False)
            filtered_df = filtered_df[mask]
        
        # 책 목록 표시 (추가 필드 포함)
        if len(filtered_df) > 0:
            display_columns = ['title', 'author', 'publisher', 'rating', 'status', 'added_date']
            
            # 추가 필드가 있으면 표시에 포함
            if 'publication_year' in filtered_df.columns and filtered_df['publication_year'].notna().any():
                display_columns.insert(-1, 'publication_year')
            if 'subject' in filtered_df.columns and filtered_df['subject'].notna().any():
                display_columns.insert(-1, 'subject')
            if 'loan_count' in filtered_df.columns and filtered_df['loan_count'].notna().any():
                display_columns.insert(-1, 'loan_count')
            
            st.dataframe(
                filtered_df[display_columns],
                use_container_width=True,
                column_config={
                    'title': '책 제목',
                    'author': '저자',
                    'publisher': '출판사',
                    'publication_year': '출판년도',
                    'subject': '주제분류',
                    'loan_count': st.column_config.NumberColumn('대출통계', format="%d회"),
                    'rating': st.column_config.NumberColumn('평점', min_value=1, max_value=5),
                    'status': '상태',
                    'added_date': '추가일'
                }
            )
            
            # 상세 보기
            st.subheader("📖 상세 정보")
            selected_book = st.selectbox("책 선택", filtered_df['title'].tolist())
            if selected_book:
                book_info = filtered_df[filtered_df['title'] == selected_book].iloc[0]
                
                col1, col2 = st.columns([2, 1])
                with col1:
                    st.write(f"**제목:** {book_info['title']}")
                    st.write(f"**저자:** {book_info['author']}")
                    st.write(f"**출판사:** {book_info['publisher']}")
                    st.write(f"**ISBN:** {book_info['isbn']}")
                    if 'publication_year' in book_info and pd.notna(book_info['publication_year']):
                        st.write(f"**출판년도:** {book_info['publication_year']}")
                    if 'subject' in book_info and pd.notna(book_info['subject']):
                        st.write(f"**주제분류:** {book_info['subject']}")
                    if 'loan_count' in book_info and pd.notna(book_info['loan_count']) and book_info['loan_count'] > 0:
                        st.write(f"**전국 대출:** {book_info['loan_count']:,}회")
                    st.write(f"**평점:** {'⭐' * int(book_info['rating'])}")
                    st.write(f"**상태:** {book_info['status']}")
                    st.write(f"**추가일:** {book_info['added_date']}")
                
                # 책 표지 표시
                with col2:
                    if 'cover_url' in book_info and pd.notna(book_info['cover_url']) and book_info['cover_url']:
                        try:
                            st.image(book_info['cover_url'], width=150, caption="책 표지")
                        except:
                            st.write("📖 표지 이미지 없음")
                    else:
                        st.write("📖 표지 이미지 없음")
                
                if pd.notna(book_info['memo']) and book_info['memo']:
                    st.write(f"**메모:** {book_info['memo']}")
                
                # 위키 링크 표시 (저장된 경우)
                if 'wiki_links' in book_info.index and pd.notna(book_info['wiki_links']):
                    try:
                        wiki_data = json.loads(book_info['wiki_links'])
                        if wiki_data:
                            st.markdown("**🌐 위키 링크:**")
                            if wiki_data.get('book') and wiki_data['book'].get('url'):
                                st.markdown(f"• [📖 책]({wiki_data['book']['url']})")
                            if wiki_data.get('author') and wiki_data['author'].get('url'):
                                st.markdown(f"• [👤 저자]({wiki_data['author']['url']})")
                    except:
                        pass
        else:
            st.info("선택한 조건에 맞는 책이 없습니다.")

elif menu == "✏️ 책 수정/삭제":
    st.header("책 정보 수정 및 삭제")
    
    df = get_books_from_db()
    
    if len(df) == 0:
        st.warning("📚 수정할 책이 없습니다. 먼저 책을 추가해주세요!")
    else:
        # 책 선택 - ID와 제목을 함께 표시
        book_options = [f"{row['title']} (ID: {row['id']})" for _, row in df.iterrows()]
        selected_book = st.selectbox("수정할 책 선택", book_options)
        
        if selected_book:
            # ID 추출
            book_id = int(selected_book.split("(ID: ")[-1].rstrip(")"))
            book_info = df[df['id'] == book_id].iloc[0].to_dict()
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.subheader("📝 정보 수정")
                
                # 현재 정보로 초기값 설정
                new_isbn = st.text_input("ISBN", value=book_info.get('isbn', '') or '')
                new_title = st.text_input("책 제목", value=book_info.get('title', ''))
                new_author = st.text_input("저자 (이름만 입력)", 
                                         value=book_info.get('author', '') or '', 
                                         help="💡 저자 이름만 입력하세요. '지은이', '옮긴이', '[저]' 등은 제거하세요. 여러 명일 경우 콤마로 구분하세요.")
                new_publisher = st.text_input("출판사", value=book_info.get('publisher', '') or '')
                
                # 태그 수정
                current_tags = book_info.get('tags', '')
                if current_tags and pd.notna(current_tags):
                    current_tag_list = [tag.strip() for tag in str(current_tags).split(',')]
                else:
                    current_tag_list = []
                
                existing_tags = get_all_tags()
                # 현재 태그 중 existing_tags에 있는 것만 default로 설정
                valid_current_tags = [tag for tag in current_tag_list if tag in existing_tags]
                
                if existing_tags:
                    new_selected_tags = st.multiselect(
                        "태그 수정",
                        options=existing_tags,
                        default=valid_current_tags,
                        help="기존 태그를 수정하거나 새로운 태그를 추가하세요"
                    )
                else:
                    new_selected_tags = []
                    st.info("아직 태그가 없습니다. 아래에서 새 태그를 추가하세요.")
                
                new_tags_input = st.text_input(
                    "새 태그 추가",
                    placeholder="태그1, 태그2 (콤마로 구분)",
                    help="새로운 태그를 추가하려면 입력하세요"
                )
                
                
                pages_val = 0
                if book_info.get('pages') and pd.notna(book_info['pages']):
                    try:
                        pages_val = int(book_info['pages'])
                    except:
                        pages_val = 0
                
                new_pages = st.number_input("페이지 수", min_value=0, value=pages_val, key="edit_pages")
                
                new_rating = st.slider("평점", 1, 5, int(book_info['rating']))
                new_status = st.selectbox("읽기 상태", ["읽고 싶음", "읽는 중", "읽음"], 
                                        index=["읽고 싶음", "읽는 중", "읽음"].index(book_info['status']))
                new_memo = st.text_area("메모", value=book_info.get('memo', '') or '')
                
                # 수정 버튼
                col_update, col_delete = st.columns(2)
                with col_update:
                    if st.button("✏️ 수정하기", type="primary", use_container_width=True, key="update_btn"):
                        # 태그 합치기
                        all_tags = new_selected_tags.copy() if new_selected_tags else []
                        if new_tags_input:
                            new_tags = [tag.strip() for tag in new_tags_input.split(',') if tag.strip()]
                            all_tags.extend(new_tags)
                        tags_str = ', '.join(all_tags) if all_tags else ''
                        
                        updated_book = {
                            'isbn': new_isbn,
                            'title': new_title,
                            'author': new_author,
                            'publisher': new_publisher,
                            'publication_year': book_info.get('publication_year', '') or '',
                            'subject': book_info.get('subject', '') or '',
                            'loan_count': book_info.get('loan_count', 0) or 0,
                            'cover_url': book_info.get('cover_url', '') or '',
                            'rating': new_rating,
                            'status': new_status,
                            'memo': new_memo,
                            'tags': tags_str,
                            'pages': new_pages
                        }
                        
                        if update_book_in_db(book_id, updated_book):
                            st.success("✅ 책 정보가 수정되었습니다!")
                            st.rerun()
                        else:
                            st.error("❌ 수정에 실패했습니다!")
                
                with col_delete:
                    # 삭제 확인을 위한 체크박스
                    confirm_delete = st.checkbox("삭제 확인", help="삭제하려면 체크하세요", key="delete_confirm")
                    if st.button("🗑️ 삭제하기", type="secondary", use_container_width=True, 
                                disabled=not confirm_delete, key="delete_btn"):
                        if delete_book_from_db(book_id):
                            st.success("✅ 책이 삭제되었습니다!")
                            st.rerun()
                        else:
                            st.error("❌ 삭제에 실패했습니다!")
            
            with col2:
                st.subheader("📖 현재 정보")
                st.write(f"**제목:** {book_info.get('title', '')}")
                
                if book_info.get('author') and pd.notna(book_info['author']):
                    st.write(f"**저자:** {book_info['author']}")
                if book_info.get('publisher') and pd.notna(book_info['publisher']):
                    st.write(f"**출판사:** {book_info['publisher']}")
                if book_info.get('isbn') and pd.notna(book_info['isbn']):
                    st.write(f"**ISBN:** {book_info['isbn']}")
                if book_info.get('publication_year') and pd.notna(book_info['publication_year']):
                    st.write(f"**출판년도:** {book_info['publication_year']}")
                if book_info.get('subject') and pd.notna(book_info['subject']):
                    st.write(f"**주제분류:** {book_info['subject']}")
                if book_info.get('loan_count') and pd.notna(book_info['loan_count']) and book_info['loan_count'] > 0:
                    st.write(f"**전국 대출:** {int(book_info['loan_count']):,}회")
                if book_info.get('tags') and pd.notna(book_info['tags']):
                    st.write(f"**태그:** {book_info['tags']}")
                if book_info.get('pages') and pd.notna(book_info['pages']) and book_info['pages'] > 0:
                    st.write(f"**페이지:** {int(book_info['pages'])}p")
                
                st.write(f"**평점:** {'⭐' * int(book_info.get('rating', 3))}")
                st.write(f"**상태:** {book_info.get('status', '')}")
                st.write(f"**추가일:** {book_info.get('added_date', '')}")
                
                # 책 표지 표시
                if book_info.get('cover_url') and pd.notna(book_info['cover_url']):
                    try:
                        st.image(book_info['cover_url'], width=150)
                    except:
                        pass
                
                if book_info.get('memo') and pd.notna(book_info['memo']):
                    st.write(f"**메모:** {book_info['memo']}")

elif menu == "📊 독서 대시보드":
    st.header("📊 내 독서 대시보드")
    
    df = get_books_from_db()
    
    if len(df) == 0:
        st.warning("📊 대시보드를 보려면 먼저 책을 추가해주세요!")
    else:
        # 자동 인사이트
        insights = get_reading_insights(df)
        if insights:
            st.subheader("💡 독서 인사이트")
            cols_insight = st.columns(len(insights) if len(insights) <= 3 else 3)
            for idx, insight in enumerate(insights[:3]):
                with cols_insight[idx % 3]:
                    st.info(insight)
        
        # 기본 통계
        st.subheader("📈 기본 통계")
        col1, col2, col3, col4, col5 = st.columns(5)
        with col1:
            st.metric("총 책 수", len(df))
        with col2:
            read_books = len(df[df['status'] == '읽음'])
            st.metric("읽은 책", read_books)
        with col3:
            reading_books = len(df[df['status'] == '읽는 중'])
            st.metric("읽는 중", reading_books)
        with col4:
            avg_rating = df['rating'].mean()
            st.metric("평균 평점", f"{avg_rating:.1f}⭐")
        with col5:
            # 올해 읽은 책
            current_year = datetime.now().year
            this_year_books = df[pd.to_datetime(df['added_date']).dt.year == current_year]
            st.metric(f"{current_year}년", f"{len(this_year_books)}권")
        
        # 인터랙티브 차트
        st.subheader("📊 독서 패턴 분석")
        
        tab1, tab2, tab3 = st.tabs(["📅 월별 추세", "📚 장르별 분포", "⭐ 평점 분석"])
        
        with tab1:
            # 월별 독서량 - 간단한 막대 차트
            try:
                df_copy = df.copy()
                df_copy['added_date_parsed'] = pd.to_datetime(df_copy['added_date'], errors='coerce')
                df_copy = df_copy[df_copy['added_date_parsed'].notna()]
                
                if len(df_copy) > 0:
                    # 최근 12개월 데이터만 표시
                    current_date = datetime.now()
                    twelve_months_ago = current_date - timedelta(days=365)
                    df_recent = df_copy[df_copy['added_date_parsed'] >= twelve_months_ago]
                    
                    # 연-월 형식으로 그룹화
                    df_recent['year_month'] = df_recent['added_date_parsed'].dt.strftime('%Y-%m')
                    
                    # 두 개의 차트 생성
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        # 월별 추가된 책 수
                        monthly_added = df_recent.groupby('year_month').size().reset_index(name='책 수')
                        monthly_added = monthly_added.sort_values('year_month')
                        
                        fig1 = px.bar(
                            monthly_added, 
                            x='year_month', 
                            y='책 수',
                            title="📚 월별 추가한 책",
                            labels={'year_month': '연-월'},
                            color='책 수',
                            color_continuous_scale='Blues',
                            text='책 수'
                        )
                        fig1.update_traces(texttemplate='%{text}', textposition='outside')
                        fig1.update_layout(showlegend=False, height=400)
                        st.plotly_chart(fig1, use_container_width=True)
                    
                    with col2:
                        # 월별 완독한 책 수 (added_date 기준으로 변경)
                        df_completed = df_recent[df_recent['status'] == '읽음'].copy()
                        
                        if len(df_completed) > 0:
                            monthly_completed = df_completed.groupby('year_month').size().reset_index(name='완독 수')
                            monthly_completed = monthly_completed.sort_values('year_month')
                            
                            fig2 = px.bar(
                                monthly_completed,
                                x='year_month',
                                y='완독 수',
                                title="✅ 월별 완독한 책",
                                labels={'year_month': '연-월'},
                                color='완독 수',
                                color_continuous_scale='Greens',
                                text='완독 수'
                            )
                            fig2.update_traces(texttemplate='%{text}', textposition='outside')
                            fig2.update_layout(showlegend=False, height=400)
                            st.plotly_chart(fig2, use_container_width=True)
                        else:
                            # 완독 데이터가 없으면 상태별 분포 표시
                            status_counts = df_recent['status'].value_counts()
                            fig2 = px.pie(
                                values=status_counts.values,
                                names=status_counts.index,
                                title="📊 독서 상태 분포",
                                hole=0.4
                            )
                            fig2.update_layout(height=400)
                            st.plotly_chart(fig2, use_container_width=True)
                else:
                    st.info("날짜 정보가 있는 책이 없습니다.")
            except Exception as e:
                st.error(f"차트 생성 오류: {e}")
        
        with tab2:
            # 장르별 분포 - 주류와 강목 분리
            if 'subject' in df.columns and df['subject'].notna().any():
                # 주제 분류 파싱 (예: "문학 > 한국문학 > 소설" 형태)
                df_subject = df[df['subject'].notna()].copy()
                
                # 주류 (첫 번째 분류) 추출
                df_subject['main_category'] = df_subject['subject'].apply(
                    lambda x: x.split('>')[0].strip() if '>' in str(x) else str(x).strip()
                )
                
                # 강목 (두 번째 분류) 추출
                df_subject['sub_category'] = df_subject['subject'].apply(
                    lambda x: x.split('>')[1].strip() if '>' in str(x) and len(x.split('>')) > 1 else None
                )
                
                col1, col2 = st.columns(2)
                
                with col1:
                    # 주류별 분포
                    main_counts = df_subject['main_category'].value_counts().head(8)
                    if len(main_counts) > 0:
                        fig = px.pie(
                            values=main_counts.values, 
                            names=main_counts.index,
                            title="📚 주류별 독서 분포",
                            hole=0.3  # 도넛 차트
                        )
                        fig.update_traces(textposition='inside', textinfo='percent+label')
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.info("주류 분류 정보가 없습니다.")
                
                with col2:
                    # 강목별 분포 - 두 가지 방식
                    if df_subject['sub_category'].notna().any():
                        # 방식 1: 주류-강목 합친 형태
                        df_with_sub = df_subject[df_subject['sub_category'].notna()].copy()
                        df_with_sub['combined'] = df_with_sub['main_category'] + ' > ' + df_with_sub['sub_category']
                        combined_counts = df_with_sub['combined'].value_counts().head(8)
                        
                        # 방식 2: 모든 강목 통합
                        all_sub_counts = df_subject['sub_category'].value_counts().head(8)
                        
                        # 탭으로 두 가지 보기 제공
                        sub_tab1, sub_tab2 = st.tabs(["주류-강목", "전체 강목"])
                        
                        with sub_tab1:
                            if len(combined_counts) > 0:
                                fig = px.bar(
                                    x=combined_counts.values,
                                    y=combined_counts.index,
                                    orientation='h',
                                    title="📖 주류-강목 분포",
                                    labels={'x': '책 수', 'y': '분류'},
                                    color=combined_counts.values,
                                    color_continuous_scale='Blues'
                                )
                                st.plotly_chart(fig, use_container_width=True)
                        
                        with sub_tab2:
                            if len(all_sub_counts) > 0:
                                fig = px.bar(
                                    x=all_sub_counts.values,
                                    y=all_sub_counts.index,
                                    orientation='h',
                                    title="📖 전체 강목별 분포",
                                    labels={'x': '책 수', 'y': '강목'},
                                    color=all_sub_counts.values,
                                    color_continuous_scale='Greens'
                                )
                                st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.info("강목 분류 정보가 없습니다.")
            else:
                st.info("장르 정보가 있는 책이 없습니다.")
        
        with tab3:
            # 평점 분석
            col1, col2 = st.columns(2)
            with col1:
                rating_count = df['rating'].value_counts().sort_index()
                fig = px.bar(x=rating_count.index, y=rating_count.values,
                           title="평점별 책 수",
                           labels={'x': '평점', 'y': '책 수'})
                fig.update_traces(marker_color=['#FF6B6B', '#FFA500', '#FFD700', '#90EE90', '#4CAF50'])
                st.plotly_chart(fig, use_container_width=True)
            
            with col2:
                # 상태별 평균 평점
                avg_rating_by_status = df.groupby('status')['rating'].mean().round(2)
                fig = px.bar(x=avg_rating_by_status.index, y=avg_rating_by_status.values,
                           title="상태별 평균 평점",
                           labels={'x': '상태', 'y': '평균 평점'})
                st.plotly_chart(fig, use_container_width=True)

elif menu == "🎯 독서 목표":
    st.header("🎯 독서 목표 관리")
    
    df = get_books_from_db()
    current_year = datetime.now().year
    
    # 목표 설정
    col1, col2 = st.columns([2, 1])
    
    with col1:
        st.subheader("📅 연간 독서 목표")
        
        # 현재 목표 조회
        current_goal = get_reading_goal(current_year)
        
        if current_goal:
            st.info(f"📚 {current_year}년 목표: **{current_goal}권**")
        else:
            st.warning(f"{current_year}년 독서 목표가 설정되지 않았습니다.")
        
        # 목표 설정/수정
        with st.form("goal_form"):
            new_goal = st.number_input(f"{current_year}년 독서 목표 (권)", min_value=1, max_value=365, value=current_goal if current_goal else 12)
            submitted = st.form_submit_button("목표 설정/수정")
            
            if submitted:
                if set_reading_goal(current_year, new_goal):
                    st.success(f"✅ {current_year}년 독서 목표를 {new_goal}권으로 설정했습니다!")
                    st.rerun()
                else:
                    st.error("목표 설정에 실패했습니다.")
    
    with col2:
        if current_goal:
            # 진행률 계산
            this_year_books = df[pd.to_datetime(df['added_date']).dt.year == current_year]
            read_books = len(this_year_books[this_year_books['status'] == '읽음'])
            progress = (read_books / current_goal) * 100
            
            st.subheader("📊 진행 상황")
            st.metric("읽은 책", f"{read_books}/{current_goal}권")
            st.progress(min(progress / 100, 1.0))
            st.write(f"달성률: **{progress:.1f}%**")
            
            # 예상 달성 날짜
            days_passed = (datetime.now() - datetime(current_year, 1, 1)).days
            if days_passed > 0 and read_books > 0:
                books_per_day = read_books / days_passed
                if books_per_day > 0:
                    days_to_goal = (current_goal - read_books) / books_per_day
                    expected_date = datetime.now() + timedelta(days=int(days_to_goal))
                    if expected_date.year == current_year:
                        st.write(f"예상 달성일: **{expected_date.strftime('%m월 %d일')}**")
                    else:
                        st.write("현재 속도로는 올해 목표 달성이 어렵습니다 😅")
    
    # 월별 진행 상황
    if current_goal and len(df) > 0:
        st.subheader("📈 월별 독서 진행")
        
        # 월별 데이터 생성
        this_year_df = df[pd.to_datetime(df['added_date']).dt.year == current_year].copy()
        this_year_df['month'] = pd.to_datetime(this_year_df['added_date']).dt.month
        
        monthly_read = this_year_df[this_year_df['status'] == '읽음'].groupby('month').size()
        monthly_cumsum = monthly_read.cumsum()
        
        # 목표 라인 생성
        months = list(range(1, 13))
        monthly_target = [current_goal * (i/12) for i in range(1, 13)]
        
        # 차트 생성
        fig = go.Figure()
        
        # 실제 읽은 책 (누적)
        fig.add_trace(go.Scatter(
            x=monthly_cumsum.index,
            y=monthly_cumsum.values,
            mode='lines+markers',
            name='실제 진행',
            line=dict(color='#4CAF50', width=3),
            marker=dict(size=8)
        ))
        
        # 목표 라인
        fig.add_trace(go.Scatter(
            x=months,
            y=monthly_target,
            mode='lines',
            name='목표 진행',
            line=dict(color='#FF9800', width=2, dash='dash')
        ))
        
        fig.update_layout(
            title="월별 누적 독서량 vs 목표",
            xaxis_title="월",
            yaxis_title="누적 책 수",
            showlegend=True,
            hovermode='x unified'
        )
        
        st.plotly_chart(fig, use_container_width=True)

elif menu == "📈 저자/출판사 분석":
    st.header("📈 저자 및 출판사 분석")
    
    df = get_books_from_db()
    
    if len(df) == 0:
        st.warning("분석할 데이터가 없습니다. 책을 추가해주세요!")
    else:
        tab1, tab2 = st.tabs(["👤 저자 분석", "🏢 출판사 분석"])
        
        with tab1:
            st.subheader("👤 저자별 분석")
            
            # TOP 저자
            col1, col2 = st.columns(2)
            
            with col1:
                st.write("**📚 가장 많이 읽은 저자 TOP 5**")
                author_counts = df['author'].value_counts().head(5)
                
                fig = px.bar(
                    x=author_counts.values,
                    y=author_counts.index,
                    orientation='h',
                    title="저자별 독서량",
                    labels={'x': '책 수', 'y': '저자'}
                )
                st.plotly_chart(fig, use_container_width=True)
            
            with col2:
                st.write("**⭐ 높은 평점을 준 저자 TOP 5**")
                author_ratings = df.groupby('author')['rating'].agg(['mean', 'count'])
                author_ratings = author_ratings[author_ratings['count'] >= 1].sort_values('mean', ascending=False).head(5)
                
                fig = px.bar(
                    x=author_ratings['mean'],
                    y=author_ratings.index,
                    orientation='h',
                    title="저자별 평균 평점",
                    labels={'x': '평균 평점', 'y': '저자'},
                    color=author_ratings['mean'],
                    color_continuous_scale='RdYlGn'
                )
                st.plotly_chart(fig, use_container_width=True)
            
            # 저자별 상세 통계
            st.write("**📊 저자별 상세 통계**")
            author_stats = df.groupby('author').agg({
                'title': 'count',
                'rating': 'mean',
                'status': lambda x: (x == '읽음').sum()
            }).round(2)
            author_stats.columns = ['총 책 수', '평균 평점', '읽은 책']
            author_stats = author_stats.sort_values('총 책 수', ascending=False).head(10)
            
            st.dataframe(author_stats, use_container_width=True)
        
        with tab2:
            st.subheader("🏢 출판사별 분석")
            
            # TOP 출판사
            col1, col2 = st.columns(2)
            
            with col1:
                st.write("**📚 가장 많이 읽은 출판사 TOP 5**")
                publisher_counts = df['publisher'].value_counts().head(5)
                
                fig = px.bar(
                    x=publisher_counts.values,
                    y=publisher_counts.index,
                    orientation='h',
                    title="출판사별 독서량",
                    labels={'x': '책 수', 'y': '출판사'}
                )
                st.plotly_chart(fig, use_container_width=True)
            
            with col2:
                st.write("**⭐ 높은 평점을 준 출판사 TOP 5**")
                publisher_ratings = df.groupby('publisher')['rating'].agg(['mean', 'count'])
                publisher_ratings = publisher_ratings[publisher_ratings['count'] >= 1].sort_values('mean', ascending=False).head(5)
                
                fig = px.bar(
                    x=publisher_ratings['mean'],
                    y=publisher_ratings.index,
                    orientation='h',
                    title="출판사별 평균 평점",
                    labels={'x': '평균 평점', 'y': '출판사'},
                    color=publisher_ratings['mean'],
                    color_continuous_scale='RdYlGn'
                )
                st.plotly_chart(fig, use_container_width=True)
            
            # 출판사별 장르 분포
            if 'subject' in df.columns and df['subject'].notna().any():
                st.write("**📚 주요 출판사의 장르 분포**")
                top_publishers = df['publisher'].value_counts().head(3).index
                
                for publisher in top_publishers:
                    publisher_books = df[df['publisher'] == publisher]
                    if 'subject' in publisher_books.columns:
                        subjects = publisher_books['subject'].value_counts().head(5)
                        if len(subjects) > 0:
                            st.write(f"**{publisher}**")
                            fig = px.pie(
                                values=subjects.values,
                                names=subjects.index,
                                title=f"{publisher} 장르 분포"
                            )
                            fig.update_traces(textposition='inside', textinfo='percent+label')
                            st.plotly_chart(fig, use_container_width=True)

elif menu == "🏷️ 태그 관리":
    st.header("🏷️ 태그 관리")
    
    df = get_books_from_db()
    all_tags = get_all_tags()
    
    if len(all_tags) == 0:
        st.warning("아직 태그가 없습니다. 책을 추가할 때 태그를 입력해보세요!")
    else:
        # 태그 통계
        st.subheader("📊 태그 통계")
        
        tag_counts = Counter()
        if 'tags' in df.columns:
            for tags in df['tags'].dropna():
                if tags:
                    for tag in tags.split(','):
                        tag = tag.strip()
                        if tag:
                            tag_counts[tag] += 1
        
        if tag_counts:
            # 태그 클라우드 효과
            st.write("**🏷️ 태그 클라우드**")
            tag_df = pd.DataFrame(tag_counts.items(), columns=['태그', '사용 횟수'])
            tag_df = tag_df.sort_values('사용 횟수', ascending=False)
            
            # 상위 20개 태그만 표시
            top_tags = tag_df.head(20)
            
            fig = px.treemap(
                top_tags,
                path=['태그'],
                values='사용 횟수',
                title="태그별 사용 빈도",
                color='사용 횟수',
                color_continuous_scale='Blues'
            )
            st.plotly_chart(fig, use_container_width=True)
            
            # 태그별 책 목록
            st.subheader("📚 태그별 책 목록")
            selected_tag = st.selectbox("태그 선택", all_tags)
            
            if selected_tag and 'tags' in df.columns:
                tagged_books = df[df['tags'].str.contains(selected_tag, case=False, na=False)]
                if len(tagged_books) > 0:
                    st.write(f"**'{selected_tag}' 태그가 있는 책들 ({len(tagged_books)}권)**")
                    display_cols = ['title', 'author', 'rating', 'status']
                    st.dataframe(tagged_books[display_cols], use_container_width=True)

elif menu == "🌐 위키 연결":
    st.header("🌐 위키피디아/Wikidata 연결")
    st.markdown("**📚 책과 저자 정보를 위키피디아와 연결하여 더 풍부한 지식을 탐색하세요!**")
    
    df = get_books_from_db()
    
    if len(df) == 0:
        st.warning("📚 위키 연결을 위해 먼저 책을 추가해주세요!")
    else:
        # 책 선택
        book_options = [f"{row['title']} - {row['author']}" for _, row in df.iterrows()]
        selected_book_option = st.selectbox("📖 위키 정보를 검색할 책 선택", book_options)
        
        if selected_book_option:
            # 선택된 책 정보 가져오기
            selected_idx = book_options.index(selected_book_option)
            book_info = df.iloc[selected_idx].to_dict()
            
            col1, col2 = st.columns([2, 1])
            
            with col1:
                st.subheader("📚 책 정보")
                st.write(f"**제목:** {book_info.get('title', '')}")
                st.write(f"**저자:** {book_info.get('author', '')}")
                st.write(f"**출판사:** {book_info.get('publisher', '')}")
                if book_info.get('subject'):
                    st.write(f"**주제:** {book_info.get('subject', '')}")
                
                # 위키 검색 버튼
                if st.button("🔍 위키피디아 검색", type="primary", use_container_width=True):
                    with st.spinner("위키피디아와 Wikidata를 검색 중입니다..."):
                        # 엔티티 검색
                        entities = wiki_connector.search_book_entities(book_info)
                        
                        # 세션 상태에 저장
                        st.session_state['wiki_entities'] = entities
                        st.session_state['wiki_searched'] = True
                        st.session_state['selected_wiki_item'] = None  # 전체 요약 선택 초기화
            
            with col2:
                if book_info.get('cover_url'):
                    try:
                        st.image(book_info['cover_url'], width=200)
                    except:
                        st.write("📖 표지 이미지 없음")
            
            # 검색 결과 표시
            if st.session_state.get('wiki_searched') and st.session_state.get('wiki_entities'):
                entities = st.session_state['wiki_entities']
                
                st.markdown("---")
                st.subheader("🔗 위키피디아 검색 결과")
                
                # 모든 검색 결과를 통합하여 표시
                found_items = []
                
                # 검색된 항목 수집
                if entities.get('book'):
                    found_items.append({
                        'type': '📖 책',
                        'data': entities['book'],
                        'key': 'book'
                    })
                
                if entities.get('author'):
                    found_items.append({
                        'type': '👤 저자',
                        'data': entities['author'],
                        'key': 'author'
                    })
                
                
                if found_items:
                    st.success(f"✅ 총 {len(found_items)}개의 위키피디아 페이지를 찾았습니다!")
                    
                    # 검색 결과 테이블
                    for idx, item in enumerate(found_items):
                        with st.container():
                            col1, col2, col3 = st.columns([1, 4, 2])
                            
                            with col1:
                                st.write(f"**{item['type']}**")
                            
                            with col2:
                                wiki_data = item['data']
                                st.write(f"**{wiki_data.get('title', '제목 없음')}**")
                                if wiki_data.get('description'):
                                    st.caption(wiki_data['description'])
                            
                            with col3:
                                col_btn1, col_btn2 = st.columns(2)
                                with col_btn1:
                                    if wiki_data.get('url'):
                                        st.link_button("🔗 위키", wiki_data['url'], use_container_width=True)
                                
                                with col_btn2:
                                    if st.button("📄 요약", key=f"summary_{item['key']}", use_container_width=True):
                                        st.session_state['selected_wiki_item'] = item
                            
                            # Wikidata 정보 표시 (저자인 경우)
                            if item['key'] == 'author' and wiki_data.get('wikidata'):
                                wikidata_info = wiki_data['wikidata']
                                with st.expander("Wikidata 정보", expanded=False):
                                    st.write(f"**ID:** {wikidata_info.get('id')}")
                                    st.write(f"**설명:** {wikidata_info.get('description')}")
                                    if wikidata_info.get('url'):
                                        st.link_button("🔗 Wikidata 페이지", wikidata_info['url'])
                            
                            st.divider()
                    
                    # 선택된 항목의 요약 표시
                    if st.session_state.get('selected_wiki_item'):
                        selected_item = st.session_state['selected_wiki_item']
                        wiki_title = selected_item['data'].get('title')
                        
                        if wiki_title:
                            st.markdown("---")
                            st.subheader(f"📄 {selected_item['type']}: {wiki_title}")
                            
                            # 요약과 전체 내용 탭
                            tab_summary, tab_full = st.tabs(["📝 요약", "📚 전체 내용"])
                            
                            with tab_summary:
                                with st.spinner("요약을 가져오는 중..."):
                                    summary = wiki_connector.get_page_summary(wiki_title)
                                    if summary and summary.get('success'):
                                        if summary.get('thumbnail'):
                                            col1, col2 = st.columns([1, 3])
                                            with col1:
                                                st.image(summary['thumbnail'], width=200)
                                            with col2:
                                                st.write(summary.get('extract', ''))
                                        else:
                                            st.write(summary.get('extract', ''))
                                        
                                        if summary.get('url'):
                                            st.link_button("🔗 위키피디아에서 전체 보기", summary['url'])
                                    else:
                                        st.error("요약을 가져올 수 없습니다.")
                            
                            with tab_full:
                                if st.button("📖 전체 내용 불러오기", key="load_full_content"):
                                    with st.spinner("전체 내용을 가져오는 중... (시간이 걸릴 수 있습니다)"):
                                        full_article = wiki_connector.get_full_article(wiki_title)
                                        
                                        if full_article and full_article.get('success'):
                                            st.session_state['full_article'] = full_article
                                        else:
                                            st.error(f"전체 내용을 가져올 수 없습니다: {full_article.get('message', '')}")
                                
                                # 전체 내용 표시
                                if st.session_state.get('full_article'):
                                    article = st.session_state['full_article']
                                    
                                    if article.get('image'):
                                        st.image(article['image'], width=300)
                                    
                                    # 내용을 섹션별로 표시
                                    content = article.get('content', '')
                                    if content:
                                        # 최대 길이 제한 (너무 긴 문서 처리)
                                        max_length = 50000
                                        if len(content) > max_length:
                                            st.warning(f"⚠️ 문서가 너무 길어 처음 {max_length}자만 표시합니다.")
                                            content = content[:max_length] + "..."
                                        
                                        # 텍스트 영역에 전체 내용 표시
                                        st.text_area(
                                            "전체 내용",
                                            content,
                                            height=600,
                                            help="전체 위키피디아 문서 내용입니다."
                                        )
                                        
                                        # 다운로드 버튼
                                        st.download_button(
                                            label="📥 텍스트 파일로 다운로드",
                                            data=article.get('content', ''),
                                            file_name=f"{wiki_title}_wikipedia.txt",
                                            mime="text/plain"
                                        )
                
                else:
                    st.info("📚 검색 결과가 없습니다.")
                    
                    # 수동 검색 링크 제공
                    st.markdown("**직접 검색하기:**")
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        search_url = f"https://ko.wikipedia.org/wiki/Special:Search?search={book_info.get('title', '')}"
                        st.link_button("📖 책 제목으로 검색", search_url, use_container_width=True)
                    
                    with col2:
                        if book_info.get('author'):
                            author_clean = clean_author_name(book_info['author'])
                            search_url = f"https://ko.wikipedia.org/wiki/Special:Search?search={author_clean}"
                            st.link_button("👤 저자명으로 검색", search_url, use_container_width=True)
                
                # 링크 저장 옵션
                if found_items:
                    st.markdown("---")
                    if st.button("💾 위키 링크 저장", help="검색된 위키 링크를 데이터베이스에 저장합니다", type="secondary"):
                        try:
                            # JSON으로 저장
                            wiki_links_json = json.dumps(entities, ensure_ascii=False)
                            
                            cursor = conn.cursor()
                            cursor.execute('''
                                UPDATE books 
                                SET wiki_links = ?, last_wiki_search = ?
                                WHERE id = ?
                            ''', (wiki_links_json, datetime.now().strftime("%Y-%m-%d %H:%M"), book_info['id']))
                            conn.commit()
                            
                            st.success("✅ 위키 링크가 저장되었습니다!")
                        except Exception as e:
                            st.error(f"저장 중 오류 발생: {e}")

elif menu == "💡 추천도서 조회":
    st.header("💡 추천도서 조회")
    st.markdown("**빅데이터 기반 추천도서 - 도서관 대출 정보를 기반으로 분석한 추천 목록**")
    
    # 추천 유형 선택
    rec_type = st.radio(
        "추천 유형 선택",
        ["📚 다독자를 위한 추천", "🎯 마니아를 위한 추천"],
        horizontal=True,
        help="다독자: 폭넓은 독서를 위한 추천 | 마니아: 특정 분야 깊이 있는 독서를 위한 추천"
    )
    
    # ISBN 입력 방식 선택
    input_method = st.radio(
        "ISBN 입력 방식",
        ["내 도서목록에서 선택", "직접 입력"],
        horizontal=True
    )
    
    selected_isbns = []
    
    if input_method == "내 도서목록에서 선택":
        df = get_books_from_db()
        if len(df) > 0 and 'isbn' in df.columns:
            # ISBN이 있는 책만 필터링
            df_with_isbn = df[df['isbn'].notna() & (df['isbn'] != '')]
            
            if len(df_with_isbn) > 0:
                # 책 선택 (복수 선택 가능)
                book_options = [f"{row['title']} ({row['isbn']})" for _, row in df_with_isbn.iterrows()]
                selected_books = st.multiselect(
                    "추천받을 책 선택 (최대 3권)",
                    book_options,
                    max_selections=3,
                    help="추천도서를 조회할 책을 선택하세요"
                )
                
                # 선택된 책의 ISBN 추출
                for book in selected_books:
                    isbn = book.split('(')[-1].rstrip(')')
                    selected_isbns.append(isbn)
            else:
                st.warning("ISBN 정보가 있는 책이 없습니다. 직접 입력을 이용해주세요.")
        else:
            st.warning("저장된 책이 없습니다. 먼저 책을 추가하거나 직접 입력을 이용해주세요.")
    
    else:  # 직접 입력
        isbn_input = st.text_area(
            "ISBN 입력 (세미콜론으로 구분, 최대 3개)",
            placeholder="예: 9788936456788;898371154X;9788937460449",
            help="10자리 또는 13자리 ISBN을 입력하세요. 여러 개 입력시 세미콜론(;)으로 구분"
        )
        
        if isbn_input:
            # 세미콜론으로 분리하고 공백 제거
            selected_isbns = [isbn.strip() for isbn in isbn_input.split(';') if isbn.strip()][:3]
    
    # 추천도서 조회 버튼
    if st.button("🔍 추천도서 조회", type="primary", disabled=len(selected_isbns) == 0):
        if selected_isbns:
            api_type = "reader" if "다독자" in rec_type else "mania"
            isbn_string = ";".join(selected_isbns)
            
            # API 호출
            url = f"http://data4library.kr/api/recommandList"
            params = {
                'authKey': LIBRARY_API_KEY,
                'isbn13': isbn_string,
                'type': api_type,
                'format': 'json'
            }
            
            with st.spinner("추천도서를 조회하는 중..."):
                try:
                    response = requests.get(url, params=params, timeout=10)
                    response.raise_for_status()
                    
                    data = response.json()
                    
                    if data.get('response') and data['response'].get('resultNum', 0) > 0:
                        recommendations = data['response'].get('docs', [])
                        
                        st.success(f"✅ {len(recommendations)}개의 추천도서를 찾았습니다!")
                        
                        # 추천도서 표시
                        st.markdown("---")
                        st.subheader("📚 추천도서 목록")
                        
                        # 데이터프레임 생성
                        rec_list = []
                        for idx, rec in enumerate(recommendations, 1):
                            book = rec.get('book', {})
                            rec_list.append({
                                '순위': idx,
                                '제목': book.get('bookname', ''),
                                '저자': book.get('authors', ''),
                                '출판사': book.get('publisher', ''),
                                '출판년도': book.get('publication_year', ''),
                                'ISBN': book.get('isbn13', '')
                            })
                        
                        rec_df = pd.DataFrame(rec_list)
                        
                        # 필터링 옵션
                        col1, col2 = st.columns(2)
                        with col1:
                            # 출판년도 필터
                            if '출판년도' in rec_df.columns:
                                years = rec_df['출판년도'].unique()
                                selected_year = st.selectbox(
                                    "출판년도 필터",
                                    ["전체"] + sorted([y for y in years if y], reverse=True)
                                )
                                
                                if selected_year != "전체":
                                    rec_df = rec_df[rec_df['출판년도'] == selected_year]
                        
                        with col2:
                            # 정렬 옵션
                            sort_by = st.selectbox(
                                "정렬 기준",
                                ["추천 순위", "최신 출판순"]
                            )
                            
                            if sort_by == "최신 출판순":
                                rec_df = rec_df.sort_values('출판년도', ascending=False)
                        
                        # 추천도서 표시
                        st.dataframe(
                            rec_df,
                            use_container_width=True,
                            hide_index=True,
                            column_config={
                                '순위': st.column_config.NumberColumn('순위', width=60),
                                '제목': st.column_config.TextColumn('제목', width=250),
                                '저자': st.column_config.TextColumn('저자', width=150),
                                '출판사': st.column_config.TextColumn('출판사', width=100),
                                '출판년도': st.column_config.TextColumn('연도', width=60),
                                'ISBN': st.column_config.TextColumn('ISBN', width=110)
                            }
                        )
                        
                    else:
                        st.warning("추천도서를 찾을 수 없습니다. ISBN을 확인해주세요.")
                        
                except requests.exceptions.RequestException as e:
                    st.error(f"API 호출 오류: {str(e)}")
                except Exception as e:
                    st.error(f"오류 발생: {str(e)}")
    
    # 사용 안내
    with st.expander("💡 추천도서 조회 안내"):
        st.markdown("""
        **추천 유형:**
        - **다독자를 위한 추천**: 폭넓은 독서를 원하는 분들을 위한 추천
        - **마니아를 위한 추천**: 특정 분야를 깊이 있게 읽고 싶은 분들을 위한 추천
        
        **사용 방법:**
        1. 내 도서목록에서 책을 선택하거나 ISBN을 직접 입력
        2. 최대 3권까지 선택 가능
        3. 추천도서는 최대 200건까지 제공
        
        **데이터 출처:**
        - 전국 도서관 대출 빅데이터 분석 결과
        - 도서관 정보나루 제공
        """)

elif menu == "📝 독서 보고서":
    st.header("📝 독서 보고서 생성")
    st.markdown("**📚 나의 독서 기록을 보고서로 만들어 공유해보세요!**")
    
    df = get_books_from_db()
    
    if len(df) == 0:
        st.warning("📚 보고서를 생성하려면 먼저 책을 추가해주세요!")
    else:
        # 보고서 유형 선택
        col1, col2 = st.columns([1, 2])
        
        with col1:
            report_type = st.radio(
                "보고서 유형",
                ["전체 기간", "월별 보고서"],
                help="전체 기간 또는 특정 월의 보고서를 생성합니다"
            )
            
            if report_type == "월별 보고서":
                # 연도와 월 선택
                current_year = datetime.now().year
                year = st.selectbox(
                    "연도",
                    range(current_year - 5, current_year + 1),
                    index=5
                )
                
                month = st.selectbox(
                    "월",
                    range(1, 13),
                    format_func=lambda x: f"{x}월",
                    index=datetime.now().month - 1
                )
            
            # 보고서 생성 버튼
            generate_btn = st.button(
                "📊 보고서 생성",
                type="primary",
                use_container_width=True
            )
        
        with col2:
            if generate_btn:
                with st.spinner("보고서를 생성하는 중..."):
                    # 보고서 생성
                    if report_type == "전체 기간":
                        report = generate_reading_report(df, period="all")
                        filename = f"reading_report_all_{datetime.now().strftime('%Y%m%d')}"
                    else:
                        report = generate_reading_report(df, period="month", year=year, month=month)
                        filename = f"reading_report_{year}{month:02d}"
                    
                    # 세션 상태에 저장
                    st.session_state['generated_report'] = report
                    st.session_state['report_filename'] = filename
        
        # 보고서 표시
        if st.session_state.get('generated_report'):
            report = st.session_state['generated_report']
            filename = st.session_state['report_filename']
            
            st.markdown("---")
            
            # 미리보기와 다운로드 탭
            tab1, tab2 = st.tabs(["📄 미리보기", "💾 다운로드"])
            
            with tab1:
                # 보고서 미리보기
                st.markdown(report)
            
            with tab2:
                st.subheader("💾 보고서 다운로드")
                
                col1, col2 = st.columns(2)
                
                with col1:
                    # 마크다운 파일 다운로드
                    st.download_button(
                        label="📝 Markdown (.md)",
                        data=report,
                        file_name=f"{filename}.md",
                        mime="text/markdown",
                        use_container_width=True,
                        help="마크다운 형식으로 다운로드 (GitHub, Notion 등에서 사용)"
                    )
                
                with col2:
                    # 텍스트 파일 다운로드
                    text_report = report.replace("#", "").replace("*", "")
                    st.download_button(
                        label="📄 Text (.txt)",
                        data=text_report,
                        file_name=f"{filename}.txt",
                        mime="text/plain",
                        use_container_width=True,
                        help="일반 텍스트 형식으로 다운로드"
                    )
        
        # 월별 트렌드 분석 (추가 기능)
        if len(df) > 0:
            st.markdown("---")
            st.subheader("📈 월별 독서 트렌드")
            
            # 최근 6개월 데이터 준비
            current_date = datetime.now()
            monthly_stats = []
            
            for i in range(6):
                target_date = current_date - timedelta(days=30*i)
                year = target_date.year
                month = target_date.month
                
                summary = generate_monthly_summary(df, year, month)
                monthly_stats.append({
                    '연월': f"{year}-{month:02d}",
                    '추가된 책': summary['total_added'],
                    '완독한 책': summary['completed'],
                    '읽은 페이지': summary['pages_read']
                })
            
            monthly_df = pd.DataFrame(monthly_stats).sort_values('연월')
            
            if not monthly_df.empty:
                # 그래프 표시
                col1, col2 = st.columns(2)
                
                with col1:
                    fig = px.bar(
                        monthly_df,
                        x='연월',
                        y=['추가된 책', '완독한 책'],
                        title='월별 독서 활동',
                        barmode='group'
                    )
                    st.plotly_chart(fig, use_container_width=True)
                
                with col2:
                    fig = px.line(
                        monthly_df,
                        x='연월',
                        y='읽은 페이지',
                        title='월별 읽은 페이지 수',
                        markers=True
                    )
                    st.plotly_chart(fig, use_container_width=True)

elif menu == "🔧 데이터 관리":
    st.header("📊 데이터 관리 센터")
    
    df = get_books_from_db()
    conn = sqlite3.connect("reading_log.db", check_same_thread=False)
    
    # 탭 구성
    tab1, tab2, tab3, tab4 = st.tabs(["📈 통계", "💾 내보내기", "📥 백업/복원", "🔧 유지보수"])
    
    with tab1:
        st.subheader("📊 데이터베이스 통계")
        
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric("📚 총 도서", f"{len(df)}권")
        
        with col2:
            if len(df) > 0:
                completed = len(df[df['status'] == '읽음'])
                st.metric("✅ 완독", f"{completed}권")
        
        with col3:
            if len(df) > 0 and 'loan_count' in df.columns:
                api_books = df[df['loan_count'].notna() & (df['loan_count'] > 0)]
                st.metric("🔗 API 연동", f"{len(api_books)}권")
        
        with col4:
            if len(df) > 0 and 'wiki_links' in df.columns:
                wiki_books = df[df['wiki_links'].notna() & (df['wiki_links'] != '{}')]
                st.metric("🌐 위키 연결", f"{len(wiki_books)}권")
        
        if len(df) > 0:
            st.markdown("---")
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.write("**📅 기록 기간**")
                st.write(f"• 최초 기록: {df['added_date'].min()}")
                st.write(f"• 최근 기록: {df['added_date'].max()}")
                
                if 'pages' in df.columns:
                    total_pages = df['pages'].sum()
                    st.write(f"• 총 페이지: {total_pages:,}페이지")
            
            with col2:
                st.write("**📊 주요 통계**")
                
                if 'rating' in df.columns:
                    avg_rating = df['rating'].mean()
                    st.write(f"• 평균 평점: ⭐ {avg_rating:.1f}")
                
                if 'tags' in df.columns:
                    all_tags = []
                    for tags in df['tags'].dropna():
                        if tags:
                            all_tags.extend(tags.split(','))
                    if all_tags:
                        st.write(f"• 총 태그: {len(set(all_tags))}개")
                
                if 'loan_count' in df.columns:
                    total_loans = df['loan_count'].sum()
                    st.write(f"• 총 대출: {total_loans:,}회")
    
    with tab2:
        st.subheader("💾 Excel 데이터 내보내기")
        
        if len(df) > 0:
            # 내보낼 컬럼 선택
            with st.expander("📋 내보낼 필드 선택", expanded=False):
                all_columns = df.columns.tolist()
                default_columns = [col for col in all_columns if col not in ['id', 'updated_date']]
                selected_columns = st.multiselect(
                    "포함할 필드",
                    all_columns,
                    default=default_columns
                )
            
            if selected_columns:
                export_df = df[selected_columns]
                
                # Excel 다운로드 버튼
                excel_data = export_to_excel(export_df)
                st.download_button(
                    label="📊 Excel 파일 다운로드",
                    data=excel_data,
                    file_name=f"reading_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    use_container_width=False
                )
                
                # 미리보기
                with st.expander("👁️ 데이터 미리보기"):
                    st.dataframe(export_df.head(10))
            else:
                st.warning("최소 하나 이상의 필드를 선택해주세요.")
        else:
            st.info("내보낼 데이터가 없습니다. 먼저 책을 추가해주세요.")
    
    with tab3:
        st.subheader("📥 백업 및 복원")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.write("**🔒 데이터베이스 백업**")
            st.write("전체 데이터베이스를 안전하게 백업합니다.")
            
            if st.button("💾 백업 파일 생성", use_container_width=True):
                try:
                    backup_data = create_backup(conn)
                    st.download_button(
                        label="⬇️ 백업 파일 다운로드",
                        data=backup_data,
                        file_name=f"reading_log_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sql",
                        mime='application/sql',
                        use_container_width=True
                    )
                    st.success("✅ 백업 파일이 준비되었습니다!")
                except Exception as e:
                    st.error(f"백업 생성 실패: {str(e)}")
        
        with col2:
            st.write("**♻️ 데이터베이스 복원**")
            st.write("백업 파일에서 데이터를 복원합니다.")
            
            uploaded_file = st.file_uploader(
                "백업 파일 선택",
                type=['db', 'sql'],
                help="이전에 생성한 백업 파일을 선택하세요"
            )
            
            if uploaded_file:
                st.warning("⚠️ 복원하면 현재 데이터가 모두 교체됩니다!")
                if st.button("🔄 데이터 복원", type="secondary", use_container_width=True):
                    if restore_from_backup(uploaded_file):
                        st.success("✅ 데이터가 성공적으로 복원되었습니다!")
                        st.rerun()
                    else:
                        st.error("❌ 복원 실패: 올바른 백업 파일인지 확인하세요.")
        
        st.info("""
        **💡 백업 권장 사항:**
        - 정기적으로 백업을 생성하여 안전한 곳에 보관하세요
        - 중요한 변경 전에는 반드시 백업을 생성하세요
        - 백업 파일은 날짜별로 구분하여 보관하세요
        """)
    
    with tab4:
        st.subheader("🔧 데이터베이스 유지보수")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.write("**📋 데이터베이스 정보**")
            
            # DB 파일 크기
            if os.path.exists("reading_log.db"):
                db_size = os.path.getsize("reading_log.db") / (1024 * 1024)  # MB
                st.write(f"• 파일 크기: {db_size:.2f} MB")
            
            st.write(f"• 파일 위치: reading_log.db")
            st.write(f"• 총 레코드: {len(df)}개")
            
            # 테이블 정보
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = cursor.fetchall()
            st.write(f"• 테이블 수: {len(tables)}개")
            
            with st.expander("📊 테이블 구조"):
                for table in tables:
                    st.write(f"**{table[0]}**")
                    cursor.execute(f"PRAGMA table_info({table[0]})")
                    columns = cursor.fetchall()
                    for col in columns:
                        st.write(f"  - {col[1]} ({col[2]})")
        
        with col2:
            st.write("**🧹 데이터 정리**")
            
            # 중복 데이터 확인
            if 'isbn' in df.columns:
                duplicates = df[df.duplicated(subset=['isbn'], keep=False)]
                if len(duplicates) > 0:
                    st.warning(f"⚠️ 중복 ISBN: {len(duplicates)}건")
                    if st.button("중복 데이터 보기"):
                        st.dataframe(duplicates[['isbn', 'title', 'author']])
            
            # 데이터 최적화
            if st.button("🔧 데이터베이스 최적화", use_container_width=True):
                try:
                    conn.execute("VACUUM")
                    conn.execute("ANALYZE")
                    st.success("✅ 데이터베이스가 최적화되었습니다!")
                except Exception as e:
                    st.error(f"최적화 실패: {str(e)}")
            
            # 캐시 초기화
            if st.button("🔄 캐시 초기화", use_container_width=True):
                st.cache_data.clear()
                st.cache_resource.clear()
                st.success("✅ 캐시가 초기화되었습니다!")
                st.rerun()

# 푸터
st.markdown("---")
st.markdown("### 📚 데이터 시대의 독서기록장")
st.markdown("**주요 기능**: ISBN 자동 검색 | 태그 관리 | 독서 목표 | 위키피디아 연결 | 독서 보고서 | Excel 내보내기")
st.markdown("**Made by**: 권민혁 | **Contact**: wiredlife@daum.net")

# 개발자 정보
st.sidebar.markdown("---")
st.sidebar.markdown("### 👨‍💻 개발자")
st.sidebar.markdown("**권민혁**")
st.sidebar.markdown("📧 wiredlife@daum.net")
