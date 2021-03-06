/*globals
 alert, pause
 */
/**
 * Workspace infrastructure for VF.
 *
 * @module $ws
 *
 * @author Naba Bana
 */

/**
 * @class $ws
 * @static
 */
var $ws = $ws || {};

(function (global) {
    "use strict";

    // Import Globals
    var $ = global.jQuery,
        trace = global.trace,
        $vf = global.$vf;

    /**
     * Represents a WorkspaceTabBar on top of VF pages for users
     *
     * @param options
     */
    $ws.WorkspaceTabBar = function (options) {

        trace('Initializing WorkspaceTabBar...');

        $.extend(this, options);

        this.render();
    };

    $ws.WorkspaceTabBar.prototype = {

        addSL: null,
        updateSL: null,
        removeSL: null,
        getSL: null,

        tabDescriptors: [],
        maxTabs: 32,

        tabContainer: null,
        moreTrigger: null,
        tabs: {},
        $addButton: null,
        tabsN: 0,
        disableEdit: false,

        render: function () {
            var i,
                curUrl = this.getCurrentHREF(),
                host = this,
                descriptor;

            if (this.tabContainer) {

                this.tabContainer.empty();

                if ($.isArray(this.tabDescriptors) && this.tabDescriptors.length) {

                    for (i = 0; i < Math.min(this.tabDescriptors.length, this.maxTabs); i++) {
                        descriptor = this.tabDescriptors[i];
                        descriptor.id = descriptor._id;

                        if (descriptor.state != undefined && descriptor.state != null){
                            descriptor.state = JSON.parse(descriptor.state);
                        }
                        if (curUrl === descriptor.href) {
                            descriptor.selected = true;
                        }
                        this.addTab(descriptor);
                    }
                } else {
                    this.showAdder();
                }

                this.tabContainer.sortable({
                    axis: 'y',
                    cancel: '.adder, .popup-menu-item-seperator, .popup-menu-item-button',
                    start: function (e, ui) {
                        ui.placeholder.height(ui.helper.height());
                    },
                    update: function ( event, ui ) {
                        host.reSort.call(host);
                    },
                    tolerance: "pointer",
                    distance: 16
                });

                this.tabContainer.disableSelection();
                this.renderTabs(true);
            }
        },

        orderedIds: function () {
            return this.tabContainer.find('.bookmark-element ').map(function(){
                return $(this).attr("data-id");
            }).get()
        },

        orderedTabs: function() {
            var oids = this.orderedIds(),
                otabs = {},
                j = oids.length,
                tab_id,
                i;

            for (tab_id in this.tabs){
                if (this.tabs.hasOwnProperty(tab_id)) {
                    i = oids.indexOf(tab_id);
                    if (i === -1){
                        i = j;
                        j++;
                    }
                    otabs[i] = this.tabs[tab_id];
                }
            }
            return otabs;
        },

        reSort: function () {

            var i,
                desc,
                that = this,
                tab_ids = this.orderedIds();

            this.tabDescriptors = $.map(tab_ids, function(tab_id, i){
                for (i = 0; i < that.tabDescriptors.length; i++){
                    desc = that.tabDescriptors[i];
                    if (desc.id === tab_id){
                        return desc;
                    }
                }
            });

            $.ajax({
                url: this.updateSL.url + '/reorder',
                type: "POST",
                data: {
                    _session_id: $.cookie( '_session_id' ),
                    tab_ids: tab_ids.join(',')
                },
                error: function(error, textStatus, errorThrown) {
                    flash('Error occurred: ' + errorThrown, 'error');
                    return false;
                }
            });

        },

        addTab: function (descriptor) {

            var that = this, tab;

            tab = new $ws.WorkspaceTab(descriptor);

            that.tabs[tab.id] = tab;
            tab.host = that;

            if (tab.selected) {
                $vf.page_state = tab.state;
            }

            tab.tabContainer = that.tabContainer;
            that.tabsN++;

            return tab;
        },

        renderTabs: function (initial) {
            var i, desc, tab;

            for (i = 0; i < this.tabsN; i++) {
                desc = this.tabDescriptors[i];
                tab = this.tabs[desc.id];
                tab.render();
            }

            if (!this.doesTabExist(this.getCurrentHREF(), $vf.page_state, initial)) {
                this.showAdder();
            } else {
                this.hideAdder();
            }
            $('#vf-bookmarks-menu-button').qtip('reposition');
        },

        removeTab: function (tabId) {
            if (this.tabs && this.tabs[tabId]) {
                var tab = this.tabs[tabId];
                delete this.tabs[tabId];
                this.tabsN--;
                tab.remove(this.reRender, this);
            }
        },

        showAdder: function () {
            if (this.tabContainer && this.getCurrentHREF() !== '/') {
                if (!this.$addButton) {
                    var host = this;
                    this.$addButton = $('<div/>', {
                        'class': 'bookmark-this-page-button popup-menu-item popup-menu-item-button inline-icon ico-bookmark',
                        text: "Bookmark this page",
                        click: function () {
                            host.addTabForCurrent();
                        }
                    });
                }
                if (!this.$addSpacer) {
                    this.$addSpacer = $('<div/>').
                        addClass('popup-menu-item-seperator');
                }
                this.$addSpacer.prependTo(this.tabContainer);
                this.$addButton.prependTo(this.tabContainer);
            }
        },

        hideAdder: function () {
            if (this.$addButton) {
                this.$addButton.remove();
                this.$addButton = null;
            }
            if (this.$addSpacer) {
                this.$addSpacer.remove();
                this.$addSpacer = null;
            }
        },

        doesTabExist: function (pathname, state, initial) {
            var selectedTab = null;
            if (initial) {
                $.each(this.tabs, function (id, tab) {
                    if (tab.selected) {
                        selectedTab = tab;
                    }
                });
            } else {
                $.each(this.tabs, function (id, tab) {
                    var position =  pathname.indexOf(tab.href);
                    if ( position !== -1 && ( tab.state !== null && Object.equals(tab.state, state)) ||
                         tab.state === null && pathname === tab.href
                        ) {
                        selectedTab = tab;
                        tab.selected = true;
                        trace('Selected tab is ' + tab.title);
                    } else {
                        tab.selected = false;
                    }

                    trace('Tab href position is ' + position + 'href is ' + tab.href);
                    trace('Href is ' + pathname)
                });
            }
            return selectedTab;
        },

        remove: function () {

            var i;

            for (i in this.tabs) {
                if (this.tabs.hasOwnProperty(i)) {
                    this.tabs[i].remove();
                }
            }
            this.tabs = {};
            this.tabContainer.remove();
        },

        closeTab: function (id) {
            this.removeTab(id);
        },

        reRender: function () {
            this.doesTabExist(this.getCurrentHREF(), $vf.page_state, false);
            this.updateTabDescriptors();
            this.renderTabs(false);
            /*this.updateSize(false);*/
        },

        getCurrentHREF: function () {
            return location.pathname + location.search + location.hash;
        },

        addTabForCurrent: function () {

            var descriptor = {
                    title: $vf.currentPage.pageTitle,
                    type: $vf.currentPage.pageType,
                    href: this.getCurrentHREF(),
                    selected: true,
                    order: this.tabDescriptors.length,
                    state: undefined
                },
                that = this;


            if ($vf.page_state) {
                descriptor.state = JSON.stringify($vf.page_state);
            }

            if (descriptor && this.tabsN <= this.maxTabs && this.addSL) {

                $.ajax({
                    url: this.addSL.url,
                    type: this.addSL.type,
                    data:$.extend( {
                        _session_id: $.cookie( '_session_id' )
                    }, descriptor),
                    success: function( data ) {
                        descriptor.id = data._id;
                        that.addTab.call(that, descriptor);
                        that.updateTabDescriptors.call(that);
                        that.renderTabs.call(that,false);

                    },
                    error: function(error, textStatus, errorThrown) {
                        flash('Problem during tab creation: ' + errorThrown, 'error')
                    }
                });
            }
        },

        updateTabDescriptors: function () {
            var i,  tab, otabs = this.orderedTabs();
            this.tabDescriptors = [];

            for (i = 0; i < this.tabsN; i++){
                tab = otabs[i];
                this.tabDescriptors.push({
                    id: tab.id,
                    title: tab.title,
                    type: tab.type,
                    href: tab.href,
                    state: tab.state
                });
            }
        },

        problem: function (errorMsg) {
            alert(errorMsg);
        }

    };

    /**
     *
     * WorkspaceTab
     *
     */
    $ws.WorkspaceTab = function (options) {
        $.extend(this, options);
    };

    $ws.WorkspaceTab.prototype = {

        id: null,
        title: null,
        type: "default",
        href: null,
        state: null,

        tabContainer: null,

        tabE: null,
        linkE: null,
        $deleteButton: null,
        host: null,
        selected: false,
        editing: false,
        $editInput: null,
        $editButton: null,

        render: function () {
            var host = this.host,
                that = this,
                tabId = this.id,
                cssString;

            if (this.tabContainer) {

                if (this.tabE) {
                    this.tabE.remove();
                }

                cssString = 'bookmark-element popup-menu-item popup-menu-item-link toolbar-container ';

                if (this.type) {
                    cssString += this.type;
                } else {
                    cssString += 'GENERIC';
                }

                if (this.selected) {
                    cssString += ' selected';
                }

                this.tabE = $('<div/>', {
                    'class': cssString,
                    'data-id': this.id
                });

                this.linkE = $('<a/>', {
                    text: this.title,
                    'class': 'bookmark-link toolbar-item toolbar-item-stretchy',
                    href: this.href,
                    mousedown: function (e) {
                        that.md_time = e.timeStamp;
                    }
                });

                this.$flagContainer = $('<span/>').
                    addClass('bookmark-flag-container toolbar-item').
                    prependTo(this.tabE);
                if (this.type !== 'default') {
                    $('<span/>').
                        addClass('flag').
                        appendTo(this.$flagContainer);
                }

                this.$deleteButton = $('<div/>', {
                    text: '',
                    href: '',
                    title: 'Remove this bookmark',
                    'class': 'bookmark-action-icon toolbar-item inline-icon ico-x',
                    click: function () {
                        host.closeTab.call(host, tabId);
                    }
                });

                this.$editButton = $('<div/>', {
                    text: '',
                    href: '',
                    title: 'Rename this bookmark',
                    'class': 'bookmark-action-icon toolbar-item inline-icon ico-edit',
                    click: function () {
                        that.triggerEdit();
                    }
                });

                this.tabContainer.append(
                    this.tabE
                        .append(this.linkE)
                        .append(this.$editButton)
                        .append(this.$deleteButton)
                );

            }
        },

        triggerEdit: function () {
            var that = this, unfocusTimout;
            if (this.host.disableEdit === false) {
                this.tabContainer.enableSelection();
                this.tabContainer.sortable("disable");
                this.$editInput = $('<input/>', {
                    "name": "ws-title",
                    "class": "rename-bookmark-title-input toolbar-item toolbar-item-stretchy",
                    "val": this.title,
                    "keydown": function (e) {
                        var newValue = String($(this).val());
                        if (e.which === 13 && newValue.length) {
                            that.updateTitle(newValue);
                        } else if (e.which === 27) {
                            that.cancelEdit();
                        }
                    }
                }).
                    css({
                        'width': this.linkE.width(),
                        'height': this.linkE.outerHeight()
                    });
                this.linkE.after(this.$editInput).detach();
                this.$editInput.
                    focus().
                    select().
                    on('blur', function () {
                        unfocusTimout = setTimeout(function () {
                            if (that.editing) {
                                that.cancelEdit();
                            }
                        }, 200);
                    });
                this.$editAcceptButton = $('<span/>').
                    addClass('bookmark-action-icon').
                    addClass('toolbar-item').
                    addClass('inline-icon ico-check_alt').
                    on('click', function () {
                        that.updateTitle(String(that.$editInput.val()));
                    }).
                    appendTo(this.tabE);
                this.$editCancelButton = $('<span/>').
                    addClass('bookmark-action-icon').
                    addClass('toolbar-item').
                    addClass('inline-icon ico-x_alt').
                    on('click', function () {
                        that.cancelEdit();
                    }).
                    appendTo(this.tabE);
                this.$editButton.
                    add(this.$deleteButton).
                    css('display', 'none');
                this.editing = true;
                this.tabE.addClass('editing');
            }
        },

        updateTitle: function (newTitle) {

            $.ajax({
                url: this.host.updateSL.url,
                type: this.host.updateSL.type,
                data: {
                    _session_id: $.cookie( '_session_id' ),
                    object_id: this.id,
                    operation: 'SET_TITLE',
                    value: newTitle
                },
                error: function(error, textStatus, errorThrown) {
                    flash('Problem during changing the title: ' + errorThrown, 'error')
                }

            });

            this.title = newTitle;
            this.linkE.text(this.title);
            this.host.updateTabDescriptors();
            this.cancelEdit();
            this.host.renderTabs(false);
            /*this.host.updateSize(false);*/
        },

        cancelEdit: function () {
            this.$editInput.after(this.linkE).remove();
            this.editing = false;
            this.tabContainer.sortable("enable");
            this.tabContainer.disableSelection();
            this.$editButton.
                add(this.$deleteButton).
                css('display', '');
            $(this.$editAcceptButton).
                add(this.$editCancelButton).
                remove();
            this.tabE.removeClass('editing');
        },
        remove: function (cb, cb_ctx) {
            var tab = this;
            this.tabE.unbind('click');
            this.$deleteButton.unbind('click');

            $.ajax({
                url: this.host.removeSL.url + this.id,
                type: this.host.removeSL.type,
                data: {
                    _session_id: $.cookie( '_session_id' ),
                    object_id: this.id
                },
                success: function() {
                    tab.tabE.hide("slide", {direction: "down"}, 200, function () {
                        tab.$deleteButton.remove();
                        tab.linkE.remove();
                        tab.tabE.remove();
                        if (cb && cb_ctx) {
                            cb.call(cb_ctx);
                        }
                    });
                },
                error: function(error, textStatus, errorThrown) {
                    flash('Problem during tab removing: ' + errorThrown, 'error')
                }

            });

        }

    };

    /**
     *
     * ReferenceBin
     *
     */
    $ws.ReferenceBin = function (options) {
        trace('Initializing ReferenceBin...');

        if ( $.cookie('referenceBinState') ) {
            this.defaultState = $.cookie('referenceBinState');
        }

        $.extend(this, options);

        this.render();

    };

    $ws.ReferenceBin.prototype = {

        containerE: null,
        horizontalOffset: null,

        defaultState: 'off',

        skinE: null,
        headerE: null,
        resizerE: null,

        referenceDescriptors: null,       // Datastructure of references to display.

        artifactLinkList: null,

        emptyMessage: null,

        addSL: null,

        removeSL: null,

        refreshIntervalID: null,

        lastMod: null,

        render: function () {

            var that = this, skinE, headerE, resizerE;

            if (this.containerE) {

                // Creating skin base

                this.skinE = skinE = $('<div/>', {
                    'class': 'referenceBin'
                });

                if ( !isNaN(this.horizontalOffset) ) {
                    this.skinE.css( 'right', this.horizontalOffset + 'px' );
                }

                this.containerE.append(skinE);


                // Creating emptyMessage

                this.emptyMessage = $('<div/>', {
                    'class': 'emptyMessage',
                    'text': 'No links in Bin'
                });

                this.skinE.append( this.emptyMessage );

                // Header of ReferenceBin Panel

                this.headerE = headerE = $('<div/>', {
                    'class': 'header'
                }).click(function() {
                        that.toggle();
                    });

                skinE.append(headerE);


                // Creating title
                $('<div/>', {
                    'class': 'title'
                }).appendTo(headerE);

                // Creating resizer
                resizerE = this.resizerE = $('<div/>', {
                    'class': 'resizer'
                }).appendTo(headerE);

                $('<div/>', {
                    'class': 'flag'
                }).appendTo(resizerE);

                // Initializing state

                this.toggle(this.defaultState);


                // Refreshing position

                this.refreshSize(false);


                // Registering refresher on scroll, resize, load and mutation events

// Not needed for the new layout
                /*
                 $(window).scroll(function () {
                 that.refreshPosition(false);
                 });


                 $(window).resize(function () {
                 that.refreshPosition(false);
                 });

                 $(window).load(function () {
                 that.refreshPosition(false);
                 });

                 this.containerE.bind('DOMSubtreeModified',function(){

                 // basically when the container's size potentially changes

                 that.refreshPosition(false);
                 });

                 // Registering refresher periodically

                 if ( !this.refreshIntervalID ) {

                 this.refreshIntervalID = setInterval( function() {

                 that.refreshPosition(true);

                 }, 300 );

                 }
                 */


                // Container for list of artifactlinks

                this.artifactLinkList = new $vf.ArtifactLinkList ({

                    containerE: skinE,
                    referenceDescriptors: this.referenceDescriptors,
                    editable: true,
                    labelMaxWidth: null,
                    maxLength: 10,
                    hideCreateLinkButton: true,

                    on_linkAdd: function(  ) {
                        that.refreshSize();
                    },

                    on_linkRemove: function( refId ) {

                        if ( that.artifactLinkList.length === 0 ) {
                            that.toggle( 'off' );
                        }

                        that.refreshSize( true );
                        that.removeReference( refId );
                    }


                });

                this.artifactLinkList.render();


                $vf.updateService.subscribe('workspace_references_last_mod',
                    function(lastMod) {
                        if (that.lastMod === undefined ||
                            String(lastMod) > String(that.lastMod)) {
                            that.lastMod = lastMod;
                            that.reloadContents();
                        }
                    }, this.lastMod);


            }

        },

        refreshSize: function(/*animate*/) {

            var h = 0, l = 0;

            if ( this.skinE ) {

                if ( this.artifactLinkList ) {
                    h = this.artifactLinkList.listE.height();
                    l = this.artifactLinkList.length;
                }

                h = Math.max( h, 92 );

                this.headerE.height( h );
                this.skinE.innerHeight( h );
                this.resizerE.height( h - 47);

//                this.refreshPosition( animate );


                if ( l === 0 ) {
                    this.emptyMessage.show();
                } else {
                    this.emptyMessage.hide();
                }

            }

        },

// Not needed for the new layout
        /*
         refreshPosition: function(animate) {

         var offset, skinHeight, maxHeight, yDiff, offsetBottom, footerE;

         if (this.skinE) {

         if ( isNaN(this.footerHeight) ) {
         footerE = $('#footer');

         if ( footerE ) {
         this.footerHeight = footerE.outerHeight();
         } else {
         this.footerHeight = 0;
         }
         }

         maxHeight = Math.min(
         $(window).height() + $(document).scrollTop() - this.footerHeight,
         this.containerE.offset().top + this.containerE.height()
         );

         offset = this.skinE.offset();
         skinHeight = this.skinE.height();
         offsetBottom = offset.top + skinHeight;

         // We want this guy to be always at the bottom of the screen or at the bottom of its container

         if ( Math.floor(offsetBottom) != Math.floor(maxHeight) ) {

         yDiff = maxHeight - ( offset.top + skinHeight );

         if ( animate !== true ) {

         this.skinE.css({
         bottom: '0px'
         });

         } else {

         this.skinE.animate( true, true );

         this.skinE.animate({
         top: '+='+yDiff
         }, 500);

         }
         }
         }

         },
         */

        //        isOn: function() {
        //
        //            return this.skinE !== null && this.skinE.hasClass('on');
        //
        //        },

        toggle: function(state) {

            var cookieValue;

            if ( this.skinE !== null) {

                if ( ( state === undefined && this.skinE.hasClass('on') ) || state === 'off') {

                    // Turning off
                    this.skinE.removeClass('on');
                    this.headerE.attr('title','Click to open Link Bin');

                    cookieValue = 'off';

                } else {

                    // Turning on

                    this.skinE.addClass('on');
                    this.headerE.attr('title','Click to close Link Bin');

                    cookieValue = 'on';
                }


                $.cookie('referenceBinState', cookieValue, { path: '/' });

            }

        },

        removeReference: function( refId ) {
            var that = this, cval = $.cookie('_session_id' ), url;
                //encodedRefId = encodeURIComponent(refId ).replace(/[!'()]/g, escape).replace(/\*/g, "%2A");

            refId = encodeURIComponent(refId);
            url = this.removeSL.url;

            if ( this.removeSL ) {

                $.ajax({
                    url: url,
                    type: this.removeSL.type,
                    data: {
                        'ref_id': refId,
                        'last_mod': that.lastMod,
                        '_session_id': cval
                    },
                    dataType: "json",
                    context: that,
                    /*
                     complete: function () {
                     },*/
                     success: function (data) {
                         trace(typeof data.last_mod);
                         that.lastMod = data.last_mod;
                     },
                     error: function (jqXHR, textStatus, errorThrown) {
                        var message;

                        if (jqXHR.responseText) {
                            try{
                                var responseData = JSON.parse(jqXHR.responseText);
                                if (responseData.detail) {
                                    message = responseData.detail;
                                }
                            } catch(err){
                                message = jqXHR.responseText;
                            }
                        }
                        if (message == null) {
                            message = errorThrown;
                        }

                        flash('Error occurred: ' + message, 'error');
                        that.reloadContents();
                     }
                });

            }

        },

        addReference: function( refId ) {

            var that = this, cval = $.cookie('_session_id');

            if ( this.addSL ) {
                $.ajax({
                    url: this.addSL.url,
                    type: this.addSL.type,
                    data: {
                        'ref_id': refId,
                        'last_mod': that.lastMod,
                        '_session_id': cval
                    },
                    dataType: "json",
                    context: that,
                    /*
                     complete: function () {
                     },*/
                    success: function (data) {
                        that.artifactLinkList.addLinkByDescriptor( data.link_descriptor );
                        that.lastMod = data.last_mod;
                        that.toggle( 'on' );
                    },
                    error: function (jqXHR, textStatus, errorThrown) {
                        var message;

                        if (jqXHR.responseText) {
                            try{
                                var responseData = JSON.parse(jqXHR.responseText);
                                if (responseData.detail) {
                                    message = responseData.detail;
                                }
                            } catch(err){
                                message = jqXHR.responseText;
                            }
                        }
                        if (message == null) {
                            message = errorThrown;
                        }

                        flash('Error occurred: ' + message, 'error');
                        that.reloadContents();
                    }
                });

            }
        },

        reloadContents: function( ) {
            var that = this, cval = $.cookie('_session_id');
            $.ajax({
                url: this.getSL.url,
                type: this.getSL.type,
                data: {},
                dataType: "json",
                context: that,
                /*
                 complete: function () {
                 },*/
                success: function (data) {
                    that.referenceDescriptors = data.contents;
                    that.artifactLinkList.referenceDescriptors = data.contents;
                    that.lastMod = data.last_mod;
                    that.toggle( 'on' );
                    that.artifactLinkList.render();
                    that.refreshSize();
                },
                error: function (error) {
                    $ws.problem(error);
                }
            });

        }


    };

    /**
     *
     * IntelliMore
     *
     */
    $ws.IntelliMore = function(options) {
        /* locals */
        var that = this,
            menuE,
            hiddenE,
            config = {},
            hiddenElements = [],
            activeElements,
            allElements,
            nextToHide,
            edgeItem,
            checkers,
            showingHidden = false,
            defaults = {
                itemSelector: '> *',
                neverHide: '.no-more-hide',
                checkers: [],
                triggerClass: 'intellitrigger',
                triggerE: null,
                hiddenClass: null,
                orientation: 'horizontal',
                autoLength: true,
                maxElements: 0,
                callout: true,
                autoPosition: true,
                bufferLen: 10,
                heightSource: $(window)
            };

        $.extend(config, defaults, options);
        menuE = config.el;
        checkers = config.checkers;
        this.triggerE =  config.triggerE;

        function findNextItem(){
            /* finds next item to be hidden */
            var item = allElements.filter(':visible')
                .not('.'+config.triggerClass)
                .not(config.neverHide)
                .last();
            if (item.length){
                nextToHide = {
                    el: item,
                    length: config.orientation === 'horizontal' ? item.outerWidth() : item.outerHeight()
                };
            } else{
                nextToHide = {
                    el: null,
                    length: 0
                };
            }
        }

        function findEdgeItem(){
            /* length checker specific -- find item on edge that will
             as well as its offset trigger length checker */
            var activeElements = allElements.filter(':visible'),
                lengthSum = 0.0,
                offset0 = 0,
                lastItem = null,
                lastLength = 0 ;
            if (config.orientation === 'horizontal'){
                $.each(activeElements, function(i, el){
                    if (i === 0){
                        offset0 = $(el).position().left;
                    }
                    lastLength = $(el).outerWidth();
                    lengthSum += lastLength;
                    lastItem = $(el);
                });
            } else {
                $.each(activeElements, function(i, el){
                    if (i === 0){
                        offset0 = $(el).position().top;
                    }
                    lastLength = $(el).outerHeight();
                    lengthSum += lastLength;
                    lastItem = $(el);
                });
            }
            if (lastItem){
                edgeItem = {
                    el: lastItem,
                    length: lastLength,
                    offset: offset0 + lengthSum - lastLength
                };
            } else {
                edgeItem = {
                    el: null,
                    length: 0
                };
            }
        }

        function check(){
            $.each(checkers, function(i, f){
                f();
            });
        }

        function registerChecker(checker){
            checkers.push(checker);
        }

        function hideHidden(){
            if (showingHidden === true){
                hiddenE.hide();
                showingHidden = false;
            }
        }

        function renderCallout(){
            var points;
            if (config.callout && hiddenE !== undefined){
                points = config.orientation === 'horizontal' ? "0,10, 10,0, 20,10" : "0,10, 10,0, 10,20";
                hiddenE.prepend(
                    '<div class="more-callout-container">' +
                        '<svg xmlns="http://www.w3.org/2000/svg" version="1.1">' +
                        '<polygon points="' + points + '"/>' +
                        '</svg>' +
                        '</div>'
                );
            }
        }

        function renderHiddenElement(){
            hiddenE = $('<div/>', {
                "class": 'more-hidden-container'
            }).css('position', 'absolute');
            if (config.hiddenClass){
                hiddenE.addClass(config.hiddenClass);
            }
            that.triggerE.append(hiddenE);
        }

        function showHidden(){
            if (showingHidden === false){
                var mh = $('<ul/>'), my, at, el;
                if (hiddenE === undefined) {
                    renderHiddenElement();
                }
                $.each(hiddenElements, function(i, h){
                    el = h.el.clone(true);
                    el.show();
                    mh.append(el);
                });
                if (config.orientation === "horizontal") {
                    my = "right top";
                    at = "right bottom";
                } else {
                    my = "left center";
                    at = "right center";
                }
                hiddenE.html(mh);
                renderCallout();
                hiddenE.show();
                if (config.autoPosition){
                    hiddenE.position({
                        my: my,
                        at: at,
                        of: that.triggerE
                    });
                }
                showingHidden = true;
            }
            return false;
        }

        function updateHidden(){
            if (showingHidden){
                showingHidden = false;
                showHidden();
            }
        }

        function popHidden() {
            nextToHide = hiddenElements.pop();
            nextToHide.el.show();
            if (hiddenElements.length === 0) {
                hideHidden();
                that.triggerE.hide();
            } else {
                updateHidden();
            }
        }

        function hideItem() {
            if (nextToHide && nextToHide.el){
                nextToHide.el.hide();
                if (hiddenElements.length === 0) {
                    that.triggerE.show();
                }
                hiddenElements.push(nextToHide);
                updateHidden();
                findNextItem();
            }
        }

        function checkElements() {
            while (activeElements.length > config.maxElements){
                hideItem();
            }
            while (activeElements.length < config.maxElements){
                popHidden();
            }
        }

        function _checkLength(windowLen, callback) {
            if (nextToHide.el && edgeItem.offset + edgeItem.length + config.bufferLen >= windowLen){
                hideItem();
                findEdgeItem();
                callback();
            } else if (hiddenElements.length &&
                (windowLen - edgeItem.offset >
                    hiddenElements[hiddenElements.length-1].length + edgeItem.length + config.bufferLen)) {
                popHidden();
                findEdgeItem();
                callback();
            }
        }

        function checkWidth() {
            var windowLen = config.heightSource.width();
            _checkLength(windowLen, checkWidth);
        }

        function checkHeight() {
            var windowLen = config.heightSource.height();
            _checkLength(windowLen, checkHeight);
        }

        function init() {
            var checker;
            allElements = menuE.find(config.itemSelector);
            activeElements = allElements;
            findNextItem();

            /* bind hide/show hidden */
            that.triggerE
                .addClass(config.triggerClass)
                .hover(showHidden, hideHidden);

            /* register auto-hide handlers */
            if (config.maxElements){
                registerChecker(checkElements);
            }
            if (config.autoLength){
                checker = config.orientation === 'horizontal' ? checkWidth : checkHeight;
                registerChecker(checker);
                findEdgeItem();
                $(window).resize(check);
            }
            check();
        }

        /* exports */
        that.init = init;
        that.check = check;
        that.updateHidden = updateHidden;

    };

    $ws.IntelliMore.prototype = {};

    $.fn.intelliMore = function(options){
        var intMore;
        options.el = $(this);
        intMore = new $ws.IntelliMore(options);
        intMore.init();
        return intMore;
    };

    /* ScrollHide */
    $ws.ScrollHide = function(el) {
        var that = this,
            hiding = false,
            hovering = false,
            headerHeight,
            triggerE;

        function rendertrigger() {
            triggerE = $('<div/>', {
                "class": "scroll-hide",
                "click": showElement
            });
            el.after(triggerE);
        }

        function hideElement() {
            hiding = true;
            el.css('opacity', 0.3);
        }

        function showElement() {
            hiding = false;
            el.css('opacity', 1.0);
        }

        function check() {
            var scrollLen = $(window).scrollTop();
            if (hiding === false && hovering === false && scrollLen > headerHeight){
                hideElement();
            } else if (hiding === true && scrollLen < headerHeight){
                showElement();
            }
        }

        function init() {
            headerHeight = el.outerHeight();
            $(window).scroll(check);
            $(el).hover(function() {
                hovering = true;
                showElement();
            }, function() {
                hovering = false;
            });
        }

        that.init = init;

    };

    $.fn.scrollHide = function() {
        var sc = new $ws.ScrollHide($(this));
        sc.init();
        return sc;
    };



}(window));
